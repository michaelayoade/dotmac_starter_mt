# ADR-0060: The response clock is a shared owner, not a support feature

- **Status:** Accepted
- **Date:** 2026-08-24
- **Decision owner:** Michael
- **Scope:** FLEET-WIDE. Applies to every Dotmac application that promises a
  response time, and to the reusable `dotmac-response-obligations` module.
- **Relates to:** ADR-0006 (product-first extraction), ADR-0008 (declaration
  registries), ADR-0024 (applications synchronize data), ADR-0059 (availability
  is chosen; ownership moves are distinct)
- **Defers to:** `dotmac-operational-escalations` for the escalation decision,
  `dotmac-durable-timers` for scheduling, Messaging/Integrator for delivery

## Context

Three products already measure response time, and none of them owns it.

**Sub** carries `sla_policies`, `sla_targets`, `sla_clocks` and `sla_breaches`
(`app/models/ticket_workflow.py`). Decisively, `entity_type` sits on both the
policy and the clock, and the enum behind it already names ticket, work order,
project and project task. Sub proved the abstraction before anyone asked for
it. The clock is durable, carries `paused_at` and `total_paused_seconds`, and
its service stages durable timers for near-breach and breach.

**ERP** computes an `SLATarget` dataclass at read time from
`DEFAULT_RESPONSE_HOURS = 24` and `DEFAULT_RESOLUTION_HOURS = 72`
(`app/services/support/sla.py`). No clock, no pause, no breach record — a
number recomputed on every page load, which cannot answer what the promise was
when the work arrived.

**CRM** keeps a third copy in `app/services/sla_assignment.py`.

Meanwhile the staffed inbox (ADR-0059) has manual escalation and no clock at
all: nothing measures first response, next response or queue wait, so the
escalation an agent raises is the only one that ever happens.

Four consumers, one already-generic durable implementation, and no owner.

## Decision

### 1. The clock is its own module

`dotmac-response-obligations` owns one promise about time on one subject: the
target that applies, the running clock, the intervals that did not count, and
the warning and breach observations produced. The tenant-only `ro` lineage owns
`mod_sla`.

Putting this inside the inbox — or inside ticketing — would hardcode a shared
capability into whichever domain asked first. Modules never import each other,
so the second consumer's only remaining option is to duplicate it, which is
exactly how three copies came to exist.

### 2. The subject is opaque

`subject_reference` is a string this module never interprets, and no ticket,
conversation, work-order, queue or agent column may exist in the schema. That
absence is what lets four domains share one clock. `subject_type` is an open
product-declared vocabulary rather than an enum edited for each adopter
(ADR-0008).

### 3. Four clocks, not one "SLA"

`FIRST_RESPONSE`, `NEXT_RESPONSE`, `QUEUE_WAIT` and `RESOLUTION` start and stop
at different moments, and a desk can hit one while missing another. Collapsing
them is how "we answered in 3 minutes" and "they waited 4 hours in the queue"
become the same number.

Targets resolve priority-specific first, then the `NULL`-priority default. A
clock binds the target row it started under, so tightening a target tomorrow
cannot retroactively breach work answered under yesterday's promise.

### 4. `due_at` is stored and moved, never derived

A resume pushes the deadline out by the time not counted. Deriving it from
`started_at + target − total_paused` at read time was rejected for two reasons:
the derived value disagrees with whatever timer the assembly already scheduled
against the old one, and it makes the sweep's `(tenant, status, due_at)` index
impossible, forcing the full-table rescan durable timers exist to avoid.

A `PAUSED` clock is excluded from the sweep entirely. Nobody owes an answer
while the promise is stopped, and sweeping one would breach a customer for a
night the desk was closed.

Completing after the deadline settles the clock as `BREACHED` with a settlement
instant. Late completion does not erase the breach, because "we answered
eventually" and "we answered in time" are different facts and only one of them
is the promise.

### 5. Business hours are a pause, not a calendar engine

There is no calendar in this module. A calendar decides when the desk is open;
the clock only needs to know it is stopped. Opening hours therefore arrive as
`PauseReason.OUTSIDE_BUSINESS_HOURS` — the product pauses at close and resumes
at open — so one time-arithmetic implementation exists rather than two.

Every pause records its reason. Sub keeps only `total_paused_seconds`, which
cannot answer the first question asked about any disputed breach: *why was this
clock stopped for fourteen hours?*

### 6. A breach is a fact; the escalation is somebody else's decision

Sub's `sla_breaches` carries open/acknowledged/resolved. That is the escalation
lifecycle `dotmac-operational-escalations` already owns "across tickets,
outages and staffed inboxes alike", and a second copy would have no
reconciliation path — the same defect ADR-0059 corrected in the inbox.

So the observation here is append-only and has **no status**. `sweep_due_clocks`
returns typed `EscalationRequested` values and enqueues two declared outbox
events; the assembly forwards them to `raise_escalation`. Severity is an opaque
string this module passes through rather than a vocabulary declared twice.

### 7. No declared permissions

Starting, pausing and completing a clock are consequences of decisions other
owners already authorized — a ticket was answered, a conversation assigned, an
agent went offline. A permission here would put a second authorization check in
front of a decision already made, which is how a legitimate state change ends
up silently not recorded.

## Consequences

- Sub is the qualifying source and first cutover. Its `SlaBreach` lifecycle rows
  do not migrate into this module; they map onto operational-escalation
  instances raised from the forwarded request, and that mapping is part of the
  cutover rather than an afterthought.
- ERP gains a durable clock it never had, and its read-time computation becomes
  a target row. CRM retires as an API consumer.
- The inbox can finally escalate automatically: ADR-0059's manual
  `escalate_conversation` gains a sibling that fires because a promise was
  missed rather than because an agent noticed.
- Kernel `0.1.0a95` allocates the namespace and is a hard floor; the module is
  unreleasable until that kernel ships.
- Adopters must drive the sweep from `dotmac-durable-timers` and route two new
  outbox event types, or breaches are recorded and nobody is told.

## Alternatives rejected

### Put the clock in `dotmac-inbox-operations`

Rejected as the mistake ADR-0059 had just corrected, one layer up. Tickets and
work orders need the same clock and cannot import the inbox module.

### Reuse the escalation policy's interval triggers

`DraftPolicyVersion` already has `unowned_after_seconds` and
`unresolved_after_seconds`. Rejected because those measure a subject's age
against wall-clock: they cannot see when a human last replied, cannot exclude
non-working hours, and cannot express a pause. Stretching them would put
response-time semantics inside the escalation owner, which is the boundary
violation in the other direction.

### Derive `due_at` at read time

Rejected — see § 4. It desynchronises scheduled timers and forces a rescan.

### Build a business-calendar engine now

Rejected because no product has one to port and rule 22 is about extracting a
qualifying implementation, not inventing one. A pause with a reason is the same
information, already needed for "waiting on customer", and it keeps one
implementation of the arithmetic. A calendar can later drive the pause; it must
not become a second way to compute elapsed time.

### Keep breach acknowledgement here, as Sub does

Rejected. Two tables answering "is this escalated and who answered" have no
reconciliation path, and the fleet already has the owner.
