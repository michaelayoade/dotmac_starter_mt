# Changelog — dotmac-inbox-operations

## 0.1.0a5 — 2026-08-23 (unreleased)

- Makes agent availability an explicit choice with four states — AVAILABLE,
  AWAY, ON_BREAK, OFFLINE. Authentication produces no presence; only an agent's
  own command does. BUSY is derived from active assignments against capacity and
  is deliberately absent from the vocabulary, so it cannot be selected or stored.
- Adds `record_presence_heartbeat`, which carries no state field: it refreshes
  freshness and can never choose a state, and an OFFLINE agent's heartbeat is
  refused rather than allowed to resurrect dispatch eligibility.
- States dispatch eligibility once, in `DISPATCHABLE_PRESENCE_STATES`, covering
  AVAILABLE only. AWAY and ON_BREAK stop NEW work and leave held work assigned
  (ADR-0059 resolves the CRM/Sub conflict in Sub's favour).
- Adds `override_agent_presence` for manager changes to another agent's state or
  capacity. Actor and reason are required by the command and by a table check
  constraint, and every transition is appended to `inbox_presence_events`.
- Adds `end_agent_session`: dispatch stops in the same transaction, and each
  held conversation becomes a durable `InboxOfflineDisposition` due after a
  configured grace period. `settle_offline_dispositions` requeues, raises a
  supervisor escalation, or retains — and cancels the disposition outright if
  the agent returns first.
- Separates the commands Sub's single "Escalate to teammate" conflated:
  `claim_conversation`, `transfer_conversation` (cold, atomic, with previous and
  new agent/queue evidence), `request_transfer`/`accept_transfer`/
  `decline_transfer`/`cancel_transfer`/`expire_transfer_requests` (warm, with an
  acceptance SLA), `requeue_conversation`, and `escalate_conversation`. Every one
  requires an actor and a reason.
- `escalate_conversation` records that an agent ASKED for an escalation and
  returns an `EscalationRequested` the product forwards to
  `dotmac-operational-escalations`, which owns whether an escalation should
  exist, under which policy version and who answered it — for tickets, outages
  and staffed inboxes alike. The inbox record is append-only, has no status,
  declares no severity vocabulary, and has no target-agent column, so ownership
  can never move and there is never a second answer to "is this escalated?".
- Requires an explicit `supervisor_override` — with its own reason — for an
  offline, at-capacity or cross-queue transfer target.
- Declares its own permission vocabulary (`inbox_operations.presence.self`,
  `.presence.manage`, `.conversation.claim`, `.conversation.transfer`,
  `.conversation.transfer_cross_team`, `.conversation.escalate`,
  `.supervisor.override`) so products stop gating inbox decisions on a ticket
  permission.
- Adds `AssignmentStatus.TRANSFERRED` and `.REQUEUED` so history distinguishes
  handed over, given back and finished. Both are terminal; the one-active-
  assignment-per-conversation index is unchanged.
- `settle_offline_dispositions` returns `escalation_requests` alongside the
  settled ids, each carrying a dedup key derived from the disposition, so a
  re-run of the sweep asks for the same escalation rather than a second one.
- Binds every actor to its subject in the COMMAND, not in a caller's promise: a
  permission proves the actor may perform the operation, never on whom.
  `SetAgentPresence`, `RecordPresenceHeartbeat` and `ClaimConversation` refuse
  unless `actor_reference` equals `agent_reference`; transfer, warm request and
  requeue refuse unless the actor is the current holder or supplies a reasoned
  `supervisor_override`; `CancelTransfer` refuses a stranger.
- `settle_offline_dispositions` locks the exact assignment it decided about and
  carries `expected_assignment_id` into the requeue, so a legitimate transfer
  during the grace period cannot leave a stale policy decision requeueing the
  new holder's live work.
- `InboxWorkflowEvent` gains `actor_reference`, so an ownership move answers who
  made it. `ReleaseConversation` accepts an optional actor (optional so the
  published a3 contract is not broken).
- Enqueues the alerts it promises. `notify_reference` previously only recorded
  whom an adapter ought to tell; three declared outbox event types now commit
  with the state change, and Messaging/Integrator owns delivery. Naming no
  target still asks for no alert.
- `AcceptTransfer` takes a fresh `eligible_agent_references`: the stored target
  proves identity, never current team eligibility at the moment of acceptance.
- `end_agent_session` records OFFLINE for an agent who never chose a state,
  instead of refusing — signing in deliberately creates no presence, so
  refusing meant an unrecordable sign-out and held work with no disposition.
- Removes `PresenceSource.HEARTBEAT`, which nothing could ever write.
- Adds settlement-coherence checks to transfer and disposition rows, a composite
  FK for `resulting_assignment_id`, and a downgrade that REFUSES on rows using a
  widened value rather than dropping the constraint.
- Migration `io_0004_availability_transfers` adds `inbox_presence_events`,
  `inbox_transfer_requests`, `inbox_escalation_requests` and
  `inbox_offline_dispositions` on the same forced-RLS plane, and widens the
  presence-state and assignment-status value checks.

## 0.1.0a4 — 2026-08-23 (unreleased)

- Adds typed identity-preserving imports for historical presence, assignment,
  queue-entry and round-robin state. Exact replays return the durable row;
  same-id/different-fact and natural-identity collisions fail closed.
- Keeps runtime commands mint-only: importing history cannot be mistaken for a
  live dispatch, promotion, release or presence consequence.

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
