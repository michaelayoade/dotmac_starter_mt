# dotmac-inbox-operations

Owns staffed inbox queues, executable provider-neutral routing, inbox-specific
agent presence, capacity-safe conversation assignment, and workflow evidence.
Version `0.1.0a3` adds durable routing decisions, active-only uniqueness for
repeatable queue and assignment lifecycles, and queue/presence row locking for
concurrent dispatch. Version `0.1.0a5` adds explicit agent availability and
separates the commands that move a conversation from the one that escalates
it.

`route_conversation` selects the lowest-priority-number active rule, admits the
conversation and records the exact rule, queue and queue-entry evidence in the
same caller-owned transaction. `promote_from_queue` chooses the agent itself
from caller-supplied opaque eligibility references, current presence, capacity
and the durable round-robin cursor; callers cannot select an arbitrary agent
through the queue path. `dispatch_queues_fairly` attempts one FIFO item from
every supplied queue, so a saturated queue cannot hide a queue with capacity.

Eligibility is an input projection, not copied Workforce state. The assembly
asks Workforce/product policy for eligible Party references and supplies them
with an explicit presence-freshness cutoff. Conversation/message content
remains with Inbox; channels/connectors remain transports; teams, skills,
shifts and field availability remain with Workforce. The tenant-only `io`
lineage owns `mod_inbox_ops`; services mutate and flush but never commit or
rollback.

First adoption uses separate typed history commands for presence, assignment,
queue-entry and round-robin state. They preserve existing UUIDs and timestamps,
validate queue relationships, and accept only an exact replay. They do not
execute routing, promotion, release or any other live workflow consequence.


## Availability is chosen, freshness is beaten, busy is derived

Three facts that inboxes routinely collapse are kept apart, each with its own
command:

- **Authentication is not availability.** Signing in writes nothing here. An
  agent is AVAILABLE, AWAY, ON_BREAK or OFFLINE because they said so.
- **A heartbeat cannot choose.** `record_presence_heartbeat` carries no state
  field, so a tab left open cannot make anyone available and a reconnect cannot
  end a break. An OFFLINE agent's heartbeat is refused outright.
- **BUSY is not a state.** `agent_availability` derives it from active
  assignments against capacity every time it is asked, so it is never stale and
  never selectable.

`DISPATCHABLE_PRESENCE_STATES` is the single statement of who receives new
work, and it covers AVAILABLE only. AWAY and ON_BREAK stop new dispatch while
leaving held conversations assigned. ADR-0059 records why CRM's behaviour of
routing to away agents was not carried forward.

`override_agent_presence` is how a manager changes somebody else's state or
capacity; the actor and reason are required by the command and by a check
constraint on `inbox_presence_events`, so an override is never afterwards
indistinguishable from the agent's own choice.

`end_agent_session` stops dispatch in the same transaction and turns each held
conversation into a durable disposition due after a configured grace period.
`settle_offline_dispositions` then requeues it, raises a supervisor escalation,
or retains it — and cancels the whole thing if the agent came back first. There
is deliberately no automatic `TRANSFER` disposition: a transfer needs a named
target and an accountable actor, and a policy can invent neither.

## Five commands, not one ambiguous button

Claim, cold transfer, warm transfer, requeue and escalate are separate
commands with separate evidence, because they are separate decisions:

| Command | Ownership | Requires |
| --- | --- | --- |
| `claim_conversation` | unassigned queued work becomes the agent's | dispatchable agent, eligible queues |
| `transfer_conversation` | moves immediately to the target | reason, actor, eligible dispatchable target |
| `request_transfer` → `accept_transfer` / `decline_transfer` | unchanged until accepted | reason, actor, acceptance SLA |
| `requeue_conversation` | returns to a queue, owned by nobody | reason, actor |
| `escalate_conversation` | **never moves** | severity, reason, alert target, dedup key |

Escalation's separation from transfer is structural: the record has no
target-agent column, so an escalation that quietly reassigns is not
expressible.

**The escalation itself belongs to `dotmac-operational-escalations`**, which
owns whether one should exist, under which immutable policy version, at what
level and who answered it — for tickets, outages and staffed inboxes alike. So
`escalate_conversation` records only the conversation-timeline fact that an
agent asked, in an append-only row with no status, no acknowledge, no resolve
and an opaque severity string rather than a vocabulary declared twice. It
RETURNS an `EscalationRequested` — modules never import each other, so the
assembly hands that to `raise_escalation` with the same `dedup_key`, and the two
records line up. Severity-must-rise and cooldown live with that owner, which is
the side that knows whether an escalation is still open.

A ticket or work-order handoff stays outside the module — ADR-0052 § 4 leaves
domain work with its own owner, and a conversation's lifecycle must be able to
end at a different time from the work it produced.

An offline, at-capacity or cross-queue target requires an explicit
`supervisor_override` carrying its own reason, so the exception is recorded as
an exception rather than looking like a routine move.

Authorization stays with the caller, but the vocabulary is declared here:
`inbox_operations.presence.self` and `.presence.manage` split changing your own
availability from changing someone else's, and `.conversation.transfer`,
`.conversation.transfer_cross_team`, `.conversation.escalate` and
`.supervisor.override` are four decisions a deployment may bind to four
different roles — rather than reusing a ticket permission for all of them.


## Who may act on whom

A permission proves the actor may perform an operation. It cannot say on whose
conversation or whose presence, so the module enforces the subject binding
itself rather than trusting the adapter:

- `SetAgentPresence`, `RecordPresenceHeartbeat` and `ClaimConversation` refuse
  unless `actor_reference` equals `agent_reference`. Without that,
  `inbox_operations.presence.self` — the one code an ordinary agent holds —
  would silently also be `presence.manage`, and the change would arrive with no
  reason attached.
- Cold transfer, warm request and requeue refuse unless the actor is the current
  holder or supplies `supervisor_override` with its own reason.
- `AcceptTransfer` and `DeclineTransfer` accept only the named target.
  `CancelTransfer` accepts the requester or the holder, or a reasoned override.

Every ownership move writes `actor_reference` on its workflow event, so the
trail answers who, not only what.

## The alerts are real

`notify_reference` used to be a note about whom someone ought to tell. The
commands now enqueue three declared outbox event types in the same transaction
as the state change — `transfer_requested`, `conversation_transferred`,
`escalation_requested` — and Messaging/Integrator owns delivery and its
outcomes. A command that names no target asks for no alert and enqueues
nothing, because an event with nowhere to go is a row the relay retries forever.
