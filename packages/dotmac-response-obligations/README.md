# dotmac-response-obligations

Owns **one promise about time on one subject**: answer within N, resolve within
M — the running clock that measures it, the paused intervals that did not
count, and the warning and breach observations it produced.

Product-first from Sub's `sla_policies` / `sla_targets` / `sla_clocks` /
`sla_breaches`, which already carried `entity_type` on both the policy and the
clock. The source proved the abstraction before anyone asked for it: this is a
shared owner, not a support feature. ERP recomputes an SLA target at read time
from `DEFAULT_RESPONSE_HOURS`, with no clock and no pause; CRM keeps a third
copy.

The tenant-only `ro` lineage owns `mod_sla`. Services mutate and flush inside
the caller's transaction; they never commit or roll back.

## What it is not

It composes with three neighbours, and the boundaries are the design:

| Owner | Answers |
| --- | --- |
| `dotmac-durable-timers` | *when* to look — scheduling |
| **this module** | *whether the promise is being kept* |
| `dotmac-operational-escalations` | *what happens about it* — policy version, level, who answered |
| Messaging / Integrator | delivery, and its outcomes |

A breach here is an append-only **observation with no status**. The source gave
breaches open/acknowledged/resolved, which is the escalation decision the
escalation owner already holds for tickets, outages and inboxes alike; a second
copy would have no reconciliation path. `sweep_due_clocks` RETURNS
`EscalationRequested` values and enqueues two declared outbox events — modules
never import each other, so the assembly carries them onward.

`subject_reference` is opaque. The module never reads what the promise is
about, which is exactly what lets a ticket, a conversation and a work order
share one clock.

## The arithmetic

`due_at` is a **stored instant that a resume moves**, not
`started_at + target − total_paused` computed at read time. Two reasons:

- A derived due time disagrees with whatever timer the assembly already
  scheduled against the old value.
- It makes the sweep's `(tenant, status, due_at)` index impossible, forcing the
  full-table rescan this module exists to avoid.

A `PAUSED` clock is excluded from the sweep entirely. Nobody owes an answer
while the promise is stopped, and sweeping one would breach a customer for a
night the desk was closed.

Completing late does **not** erase a breach: the clock settles as `BREACHED`
with a settlement instant, because "we answered eventually" and "we answered in
time" are different facts and only one of them is the promise.

## Four clocks, not one "SLA"

`FIRST_RESPONSE`, `NEXT_RESPONSE`, `QUEUE_WAIT` and `RESOLUTION` start and stop
at different moments, and a desk can hit one while missing another. Collapsing
them is how "we answered in 3 minutes" and "they waited 4 hours in the queue"
become the same number.

Targets resolve priority-specific first, then the `NULL`-priority default — so
a desk states "4 hours, except urgent which is 30 minutes" in two rows rather
than one row per priority forever. A partial unique index makes "one default
per policy and kind" real, since PostgreSQL permits many NULLs in a `UNIQUE`.

A clock binds the target row it started under, so tightening a target tomorrow
cannot retroactively breach work answered under yesterday's promise.

## Business hours, without a calendar engine

There is deliberately no calendar in this module. A calendar is a *policy* that
decides when the desk is open; the clock only needs to know that it is stopped.
So business hours reach it as `PauseReason.OUTSIDE_BUSINESS_HOURS` — the
product pauses at close and resumes at open, and `sla_clock_pauses` records the
interval.

That keeps one time-arithmetic implementation instead of two, and it means the
same mechanism answers "waiting on the customer", "waiting on a third party"
and "suspended by an operator". Every pause carries its reason, because a pause
with no recorded reason cannot answer the first question asked about any
disputed breach: *why was this clock stopped for fourteen hours?*

## No declared permissions

Starting, pausing and completing a clock are consequences of decisions other
owners already authorized — a ticket was answered, a conversation was assigned,
an agent went offline. A permission here would put a second authorization check
in front of a decision already made, which is how a legitimate state change
ends up silently not recorded.
