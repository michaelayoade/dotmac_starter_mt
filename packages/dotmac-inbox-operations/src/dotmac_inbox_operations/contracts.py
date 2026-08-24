"""Staffed inbox operation commands and states."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InboxOperationsError(Exception):
    """Base inbox-operations refusal."""


class Conflict(InboxOperationsError):
    """The requested inbox operation is inadmissible."""


class PresenceState(enum.StrEnum):
    """The four states an agent may explicitly hold.

    Signing in is authentication and produces no state here; an agent chooses
    one of these and nothing else infers it. `BUSY` is deliberately absent: it
    is derived from active assignments against capacity (`AgentAvailability`),
    so it can never be selected, faked, or left stale by a browser that closed.
    """

    AVAILABLE = "AVAILABLE"
    AWAY = "AWAY"
    ON_BREAK = "ON_BREAK"
    OFFLINE = "OFFLINE"


# The one place dispatch eligibility is stated. CRM dispatches to online AND
# away agents; Sub dispatches only to online. ADR-0059 resolves the conflict in
# Sub's favour: a paused agent is paused, so AWAY and ON_BREAK stop NEW work
# while leaving work they already hold assigned to them.
DISPATCHABLE_PRESENCE_STATES: frozenset[PresenceState] = frozenset(
    {PresenceState.AVAILABLE}
)


class PresenceSource(enum.StrEnum):
    """Who caused a presence transition — the audit distinction that makes a
    manager override reviewable as something other than the agent's own choice.

    There is no HEARTBEAT member because a heartbeat causes no transition; a
    declared source nothing can ever write is a claim the data does not
    support.
    """

    AGENT = "AGENT"
    MANAGER = "MANAGER"
    SESSION = "SESSION"


class AssignmentStatus(enum.StrEnum):
    """Only `ASSIGNED` is active; every other member is durable history.

    The three terminal members are distinct on purpose. "This agent stopped
    holding the conversation" is not one fact: a release ends the work, a
    transfer moves it to a named agent, and a requeue returns it to the line.
    Collapsing them loses the answer to "why did this conversation move?".
    """

    ASSIGNED = "ASSIGNED"
    RELEASED = "RELEASED"
    TRANSFERRED = "TRANSFERRED"
    REQUEUED = "REQUEUED"


class QueueEntryStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    PROMOTED = "PROMOTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class AdmitToQueue:
    """Admit one conversation to the back of a queue.

    `conversation_reference` is opaque: this module owns the ORDER, never the
    conversation. Admission is idempotent on that reference, because a retried
    inbound webhook must not take two places in the line.
    """

    queue_id: UUID
    conversation_reference: str
    entered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PromoteFromQueue:
    queue_id: UUID
    eligible_agent_references: tuple[str, ...]
    presence_fresh_after: datetime
    promoted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CreateQueue:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateRoutingRule:
    queue_id: UUID
    channel_code: str
    priority: int


@dataclass(frozen=True, slots=True)
class SetAgentPresence:
    """An agent's own choice. `actor_reference` MUST equal `agent_reference`.

    The equality is checked here rather than trusted to the caller because a
    permission proves the actor may perform the OPERATION, never which subject
    it may perform it on. Without this, `inbox_operations.presence.self` — the
    one code an ordinary agent holds — would let anyone set anyone's state and
    capacity, which is `presence.manage` by another route. Changing someone
    else's presence has its own command, and that one demands a reason.
    """

    agent_reference: str
    state: PresenceState
    assignment_capacity: int
    observed_at: datetime
    actor_reference: str


@dataclass(frozen=True, slots=True)
class ImportAgentPresence:
    """Identity-preserving historical presence snapshot."""

    id: UUID
    agent_reference: str
    state: PresenceState
    assignment_capacity: int
    observed_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ImportConversationAssignment:
    """Identity-preserving historical assignment record."""

    id: UUID
    conversation_reference: str
    queue_id: UUID
    agent_reference: str
    status: AssignmentStatus
    assigned_at: datetime
    released_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ImportQueueEntry:
    """Identity-preserving historical FIFO admission record."""

    id: UUID
    queue_id: UUID
    conversation_reference: str
    queue_position: int
    status: QueueEntryStatus
    entered_at: datetime
    settled_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ImportRoundRobinCursor:
    """Identity-preserving historical rotation snapshot."""

    id: UUID
    queue_id: UUID
    last_assigned_agent_reference: str | None
    rotation_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssignConversation:
    conversation_reference: str
    queue_id: UUID
    agent_reference: str
    assigned_at: datetime
    eligible_agent_references: tuple[str, ...]
    presence_fresh_after: datetime


@dataclass(frozen=True, slots=True)
class ReleaseConversation:
    assignment_id: UUID
    released_at: datetime
    reason: str
    # Optional, not required: the published a3 contract has no actor, and
    # breaking it to complete the trail would cost more than it buys. New
    # callers should supply it.
    actor_reference: str | None = None


@dataclass(frozen=True, slots=True)
class RouteConversation:
    """Resolve and durably record one routing decision.

    The reference belongs to the ingress adapter and makes delivery replay
    idempotent. The channel is a provider-neutral Inbox vocabulary member.
    """

    decision_reference: str
    conversation_reference: str
    channel_code: str
    routed_at: datetime


@dataclass(frozen=True, slots=True)
class RoutedConversation:
    decision_id: UUID
    rule_id: UUID
    queue_id: UUID
    queue_entry_id: UUID
    queue_position: int


@dataclass(frozen=True, slots=True)
class QueueEligibility:
    """A Workforce/product eligibility projection for one dispatch attempt."""

    queue_id: UUID
    agent_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DispatchQueues:
    """Attempt one FIFO promotion per queue so a blocked queue cannot starve peers."""

    queues: tuple[QueueEligibility, ...]
    dispatched_at: datetime
    presence_fresh_after: datetime


# ── Availability ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecordPresenceHeartbeat:
    """Keep an already-chosen state fresh; it can never CHOOSE one.

    The heartbeat carries no state field at all. That is the whole point: a
    browser tab left open must not be able to make an agent available, and a
    reconnect must not silently undo a break. Freshness and choice are separate
    facts with separate commands, which is what stops a stale tab from holding
    dispatch eligibility open after the person walked away.
    """

    agent_reference: str
    observed_at: datetime
    actor_reference: str


@dataclass(frozen=True, slots=True)
class OverrideAgentPresence:
    """A manager changes another agent's state or capacity.

    Actor and reason are REQUIRED fields of the command rather than optional
    context, because an override with no recorded reason is indistinguishable
    afterwards from the agent's own choice. The module never decides whether the
    actor is a manager — that is `inbox_operations.presence.manage`, held by the
    caller — but it refuses to record the change without the evidence.
    """

    agent_reference: str
    actor_reference: str
    reason: str
    observed_at: datetime
    state: PresenceState | None = None
    assignment_capacity: int | None = None


@dataclass(frozen=True, slots=True)
class AgentAvailability:
    """The derived answer to "can this agent take another conversation?"

    `busy` is computed here and stored nowhere. An agent at capacity is busy
    whether or not anyone told the system so, and stops being busy the moment a
    conversation closes — a stored flag would be wrong in both directions.
    """

    agent_reference: str
    state: PresenceState
    assignment_capacity: int
    active_assignments: int
    observed_at: datetime
    presence_fresh: bool
    busy: bool
    dispatchable: bool


class OfflineDisposition(enum.StrEnum):
    """What happens to conversations an agent still holds when they go offline.

    `TRANSFER` is deliberately not a member. A transfer needs a named target and
    an accountable actor; a policy cannot invent either. `ESCALATE` raises the
    supervisor alert that puts a human in front of that decision, which is the
    honest automated step.
    """

    RETAIN = "RETAIN"
    ESCALATE = "ESCALATE"
    REQUEUE = "REQUEUE"


class DispositionStatus(enum.StrEnum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class EndAgentSession:
    """Sign-out or forced offline: stop dispatch NOW, settle held work later.

    The two halves are separated in time on purpose. Dispatch eligibility ends
    in this transaction, so no further conversation can be pushed to a person
    who has gone. Conversations they already hold enter the grace period the
    product configured, and `settle_offline_dispositions` acts on them when it
    expires — unless the agent comes back available first, which cancels them.
    """

    agent_reference: str
    occurred_at: datetime
    disposition: OfflineDisposition
    grace_seconds: int
    reason: str
    actor_reference: str | None = None
    notify_reference: str | None = None
    escalation_severity: str | None = None


@dataclass(frozen=True, slots=True)
class AgentSessionEnded:
    """Dispatch has already stopped; these are the conversations still held."""

    agent_reference: str
    state: PresenceState
    disposition_ids: tuple[UUID, ...]
    due_at: datetime | None


@dataclass(frozen=True, slots=True)
class SettleOfflineDispositions:
    settled_at: datetime
    presence_fresh_after: datetime
    limit: int = 50


@dataclass(frozen=True, slots=True)
class OfflineDispositionsSettled:
    """`escalation_requests` is the sweep's outbound work: the product forwards
    each one to the escalation owner. Returned rather than delivered, because
    modules never import each other."""

    requeued: tuple[UUID, ...]
    escalated: tuple[UUID, ...]
    retained: tuple[UUID, ...]
    cancelled: tuple[UUID, ...]
    escalation_requests: tuple[EscalationRequested, ...] = ()


# ── Ownership movement ──────────────────────────────────────────────────────


class TransferKind(enum.StrEnum):
    COLD = "COLD"
    WARM = "WARM"


class TransferStatus(enum.StrEnum):
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ClaimConversation:
    """An agent pulls unassigned queued work to themselves.

    A claim is a pull, not the push `AssignConversation` models, so the claiming
    agent must satisfy the same availability and capacity rules dispatch would
    have applied. `eligible_queue_ids` is required and may not be empty: a claim
    whose queue scope defaults to "anything" is a cross-team claim nobody
    authorized.
    """

    conversation_reference: str
    agent_reference: str
    claimed_at: datetime
    presence_fresh_after: datetime
    eligible_queue_ids: tuple[UUID, ...]
    actor_reference: str


@dataclass(frozen=True, slots=True)
class TransferConversation:
    """Cold transfer: ownership moves to the target in this transaction.

    Reason and actor are required. Without an override the target must be
    dispatchable and inside `eligible_agent_references`, and the conversation
    must stay in its current queue. Every departure from that — an offline
    target, a target at capacity, a different queue — needs `supervisor_override`
    with its own reason, so the exception is recorded as an exception rather
    than being indistinguishable from a routine move.
    """

    conversation_reference: str
    to_agent_reference: str
    reason: str
    actor_reference: str
    transferred_at: datetime
    presence_fresh_after: datetime
    eligible_agent_references: tuple[str, ...] = ()
    to_queue_id: UUID | None = None
    supervisor_override: bool = False
    override_reason: str | None = None
    notify_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationTransferred:
    """Both sides of the move, so the trail answers "from whom, to whom"."""

    request_id: UUID
    previous_assignment_id: UUID
    new_assignment_id: UUID
    from_agent_reference: str
    to_agent_reference: str
    from_queue_id: UUID
    to_queue_id: UUID
    notify_reference: str | None


@dataclass(frozen=True, slots=True)
class RequestTransfer:
    """Warm transfer: ask, and stay responsible until someone answers.

    The original assignment is untouched while the request is open. `expires_at`
    is the acceptance SLA — a request nobody answers must fail back to its
    owner, not hang forever looking like it was handled.
    """

    conversation_reference: str
    to_agent_reference: str
    reason: str
    actor_reference: str
    requested_at: datetime
    expires_at: datetime
    presence_fresh_after: datetime
    eligible_agent_references: tuple[str, ...] = ()
    to_queue_id: UUID | None = None
    supervisor_override: bool = False
    override_reason: str | None = None
    notify_reference: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptTransfer:
    """Only the named target may accept, and only while still eligible."""

    request_id: UUID
    actor_reference: str
    accepted_at: datetime
    presence_fresh_after: datetime
    # Re-supplied at ACCEPT, not read from the request: team membership can
    # change between asking and answering, and the stored target proves
    # identity, never current eligibility.
    eligible_agent_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeclineTransfer:
    request_id: UUID
    actor_reference: str
    declined_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class CancelTransfer:
    """Withdraw an open request. The requester may; anyone else overrides."""

    request_id: UUID
    actor_reference: str
    cancelled_at: datetime
    reason: str
    supervisor_override: bool = False
    override_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExpireTransferRequests:
    expired_at: datetime
    limit: int = 50


@dataclass(frozen=True, slots=True)
class RequeueConversation:
    """The holder gives the conversation back to a queue, owning no one.

    Distinct from a transfer because there is no target agent, and distinct from
    a release because the work is not finished — it re-enters the line at the
    back and dispatch may promote it again.

    Only the holder may requeue their own work; anyone else needs
    `supervisor_override` with its own reason. Giving a colleague's
    conversation back to the queue is a supervisor act, not a peer one.
    """

    conversation_reference: str
    reason: str
    actor_reference: str
    requeued_at: datetime
    queue_id: UUID | None = None
    # Set by the offline sweep: the assignment it decided about. The requeue
    # refuses if the conversation has since moved to a different assignment,
    # which is what stops a stale policy decision from taking live work off
    # whoever holds it now.
    expected_assignment_id: UUID | None = None
    supervisor_override: bool = False
    override_reason: str | None = None


# ── Escalation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EscalateConversation:
    """Ask for an escalation and record the asking on the conversation.

    This module does NOT decide whether an escalation should exist, at what
    level, on which channel, or when it is answered — `dotmac-operational-
    escalations` owns that across tickets, outages and inboxes alike, and a
    second owner here would be two records answering "is this escalated?".

    What belongs to the inbox is narrower and real: an agent, working a
    conversation, asked for one, and the conversation's own timeline should say
    so. `severity` is therefore an opaque string passed straight through to the
    escalation owner rather than a vocabulary this module declares. `dedup_key`
    is the caller's idempotency handle and is the same key the escalation owner
    will dedupe on, so a retried request cannot become two escalations.

    Ownership never moves. The record has no target-agent column to write.
    """

    conversation_reference: str
    severity: str
    reason: str
    actor_reference: str
    notify_reference: str
    requested_at: datetime
    dedup_key: str
    due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EscalationRequested:
    """Everything the product needs to raise this with the escalation owner.

    Returned rather than delivered: modules never import each other, so the
    assembly is what carries this to `raise_escalation`. The `dedup_key` is
    passed through unchanged so the two records line up afterwards.
    """

    request_id: UUID
    conversation_reference: str
    severity: str
    reason: str
    notify_reference: str
    dedup_key: str
    assignment_id: UUID | None
    due_at: datetime | None


__all__ = [
    "AcceptTransfer",
    "AdmitToQueue",
    "AgentAvailability",
    "AgentSessionEnded",
    "AssignConversation",
    "AssignmentStatus",
    "CancelTransfer",
    "ClaimConversation",
    "Conflict",
    "ConversationTransferred",
    "CreateQueue",
    "CreateRoutingRule",
    "DISPATCHABLE_PRESENCE_STATES",
    "DeclineTransfer",
    "DispatchQueues",
    "DispositionStatus",
    "EndAgentSession",
    "EscalateConversation",
    "EscalationRequested",
    "ExpireTransferRequests",
    "ImportAgentPresence",
    "ImportConversationAssignment",
    "ImportQueueEntry",
    "ImportRoundRobinCursor",
    "InboxOperationsError",
    "OfflineDisposition",
    "OfflineDispositionsSettled",
    "OverrideAgentPresence",
    "PresenceSource",
    "PresenceState",
    "PromoteFromQueue",
    "QueueEligibility",
    "QueueEntryStatus",
    "RecordPresenceHeartbeat",
    "ReleaseConversation",
    "RequestTransfer",
    "RequeueConversation",
    "RouteConversation",
    "RoutedConversation",
    "SetAgentPresence",
    "SettleOfflineDispositions",
    "TransferConversation",
    "TransferKind",
    "TransferStatus",
]
