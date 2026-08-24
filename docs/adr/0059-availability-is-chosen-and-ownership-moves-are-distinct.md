# ADR-0059: Availability is chosen; ownership moves are distinct commands

- **Status:** Accepted
- **Date:** 2026-08-23
- **Decision owner:** Michael
- **Scope:** FLEET-WIDE. Applies to every Dotmac application that staffs an
  inbox and to the reusable `dotmac-inbox-operations` module.
- **Relates to:** ADR-0008 (declaration registries), ADR-0014 (at-most-once
  execution), ADR-0024 (applications synchronize data), ADR-0052 (conversations
  own threads, not transport or workforce policy)
- **Defers to:** `dotmac-operational-escalations` for the escalation decision
- **Supersedes in part:** the CRM dispatch behaviour described below

## Context

Two live implementations disagree, and neither is internally consistent.

**Availability.** Sub carries four backend presence states but exposes three in
the operator UI (`templates/admin/inbox/_sidebar.html`), and has no inbox
heartbeat at all — a presence row goes stale silently and nothing distinguishes
"available" from "available three hours ago". CRM does have browser presence
heartbeats and shift-duration reporting (`app/web/admin/crm_presence.py`), but
routes work to online **and** away agents
(`app/services/crm/inbox/routing.py`), so an agent who marked themselves away
keeps receiving conversations. Neither product separates authentication from
availability, and neither derives busy: CRM computes a concurrency cap at
dispatch time but exposes no availability answer a UI or a supervisor can read.

**Ownership movement.** Sub's conversation UI offers one action, "Escalate to
teammate" (`templates/admin/inbox/_conversation.html`). Behind it,
`escalate_conversation` (`app/services/team_inbox_assignment.py`) branches
between assigning to a named person, auto-assigning, and queueing for a team.
Three different outcomes share one name, one button and one audit story. There
is no warm transfer, no transfer reason, no acceptance SLA, and no record of who
held the conversation before. Escalation — raising urgency and alerting a lead —
does not exist as a thing separate from reassignment.

**Authorization.** Both products gate inbox actions on broad existing
permissions, notably `support:ticket:update`. That makes "may edit a ticket" and
"may take a conversation away from a colleague" the same decision, so no
deployment can separate them without a code change.

## Decision

### 1. Authentication, choice, freshness and load are four separate facts

Signing in creates no availability. An agent holds exactly one of four explicit
states — `AVAILABLE`, `AWAY`, `ON_BREAK`, `OFFLINE` — and only their own
command or a manager override sets it.

A browser heartbeat refreshes freshness and carries **no state field**, so a tab
left open cannot make anyone available and a reconnect cannot end a break. A
heartbeat for an `OFFLINE` agent is refused rather than ignored.

`BUSY` is not a member of the vocabulary. It is derived from active assignments
against capacity at read time, so it cannot be selected, cannot be stored, and
cannot be stale in either direction.

### 2. Only AVAILABLE is dispatchable

Dispatch eligibility is stated in exactly one place and covers `AVAILABLE`
alone. `AWAY` and `ON_BREAK` stop NEW dispatch and leave already-held
conversations assigned to the agent; `OFFLINE` stops dispatch in the same
transaction as the sign-out.

CRM's behaviour of routing to away agents is **not** carried forward. A paused
agent is paused: "away" that still receives work is a status display, not a
control, and it teaches agents that the control does not work.

### 3. Held work follows a durable, configured policy — never an invented transfer

Ending a session turns each still-held conversation into a durable disposition
row with a due time, settled after the configured grace period by requeueing,
raising a supervisor escalation, or retaining. A disposition is cancelled if the
agent returns and is dispatchable before it comes due.

There is deliberately no automatic `TRANSFER` disposition. A transfer needs a
named target and an accountable actor and a policy can invent neither; the
supervisor alert is the honest automated step, and a human makes the move.

The state is rows, not scheduler memory, for the reason the round-robin cursor
is already durable: a queue of pending decisions in a worker process is lost on
the next deploy.

### 4. Claim, transfer, requeue and escalate are distinct commands

| Command | Ownership effect |
| --- | --- |
| Claim | unassigned queued work becomes owned by the claiming agent |
| Cold transfer | ownership moves immediately to another eligible agent |
| Warm transfer | target accepts or declines; the original agent remains responsible until then |
| Requeue | the holder releases the work back to a team queue, owned by nobody |
| Escalate | records that an agent ASKED for one; **ownership does not move** |

Every one requires an actor and a reason, and records the previous and new agent
and queue. A warm transfer carries an acceptance SLA: an unanswered request
expires back to its owner rather than reading forever as "being handled".

Escalation's separation is structural rather than conventional — the escalation
record has no target-agent column, so an escalation that quietly reassigns is
not expressible. A re-escalation must RAISE severity; repeating the open level
is a reminder, and calling it an escalation is how an SLA clock gets reset by
something that changed nothing.

A ticket or work-order handoff is a product consequence with its own owner
(ADR-0052 § 4), not a conversation lifecycle transition. The conversation and
the domain work must be able to end at different times.

### 5. Exceptions are recorded as exceptions

An offline, at-capacity or cross-queue transfer target requires an explicit
supervisor override carrying its own reason. Without it the module refuses. With
it the override and its reason are stored on the transfer record, so the
exception is reviewable rather than indistinguishable from a routine move.

### 6. Inbox decisions get their own declared permissions

`dotmac-inbox-operations` declares and owns the vocabulary — self-presence,
managing another agent's presence, claim, transfer, cross-team transfer,
escalation and supervisor override. Per ADR-0008 these may only be REFERENCED
elsewhere. Reusing `support:ticket:update` for any of them stops.

Only self-presence, claim, transfer and escalate reach an ordinary agent by
default. Managing someone else's availability, cross-team transfer and
supervisor override start with supervisors and admins, because widening a grant
is a deliberate act and narrowing one afterwards is an incident.

### 7. A permission proves the operation, never the subject

Actor-to-subject binding belongs in the command shape, enforced by the owner.
Self-presence and claim commands carry an `actor_reference` that must equal the
agent; transfer and requeue require the current holder or a reasoned supervisor
override. A module that accepts an arbitrary subject has turned its narrowest
permission into its broadest one, and the resulting change carries no reason.

### 8. A recorded notification target is not a notification

Storing `notify_reference` records whom an adapter ought to tell. The command
that promises an alert enqueues a declared outbox event in the same transaction
as the state change; delivery and its outcomes stay with Messaging/Integrator. A
command that names no target asks for no alert.

## Consequences

- Sub gains a fourth exposed state and its first inbox heartbeat; CRM's
  away-is-dispatchable routing is a behaviour change at its cutover, and agents
  who relied on away still receiving work will notice.
- "Escalate to teammate" splits into five buttons with five audit stories. The
  UI work is a product change in each adopter; the module refuses the ambiguous
  combined command outright.
- Products must supply an actor and a reason for every ownership move. Callers
  that previously reassigned anonymously will fail closed until they do.
- Deployments must bind seven new permission codes. Until they do, only the
  declared default roles hold them.
- Callers must supply an actor for their own presence, heartbeat and claim
  commands, and must be the holder (or override) to move a conversation.
  Adapters that previously passed a bare agent reference fail closed.
- Three outbox event types must be declared by the installed manifest set and
  routed to delivery, or the alerts are enqueued and never sent.
- `AssignmentStatus` gains `TRANSFERRED` and `REQUEUED`. Both are terminal and
  the one-active-assignment-per-conversation index is unchanged, but readers
  that treated `!= ASSIGNED` as "released" now have a finer answer available.

## Alternatives rejected

### Keep away dispatchable, as CRM does

Rejected because it makes the control a lie. An agent who selects away and keeps
receiving conversations learns that presence does not work, and the next thing
they do is close the tab — which is strictly worse, because now the system has
no signal at all.

### Store BUSY as a fifth state

Rejected because it is wrong in both directions within seconds: wrong the moment
a conversation closes, and wrong the moment one opens. Anything derived from
load must be derived from load.

### Let a heartbeat carry the current state

Rejected because it erases the distinction the slice exists to draw. A heartbeat
that can assert AVAILABLE means a browser, not a person, decides availability —
and a reconnect silently ends a break the agent is still on.

### An inbox-owned escalation record

Rejected after the fact — an earlier draft of this slice shipped an
`inbox_escalations` table with OPEN/ACKNOWLEDGED/RESOLVED and a declared
severity enum. That is a second writer of a decision
`dotmac-operational-escalations` already owns for inboxes by name, and two
tables answering "is this escalated?" have no reconciliation path. The inbox
keeps the ask; the owner keeps the escalation.

### Automatic transfer as an offline disposition

Rejected because the module would have to pick the target itself. That is either
a hidden dispatch decision made without the receiving agent's consent, or a
guess; the supervisor alert puts the choice in front of someone accountable.

### One `move_conversation` command with a mode flag

Rejected as the same defect Sub already has, one layer down. A flag makes claim,
cold transfer, warm transfer, requeue and escalate share a call site, a
permission and an audit row, and the trail goes back to being unable to answer
what happened.

### Keep gating on `support:ticket:update`

Rejected because it binds two genuinely different decisions to one grant. A
deployment that wants agents to edit tickets but not to take work off each other
cannot express it, and the fix is a code change rather than a role change.
