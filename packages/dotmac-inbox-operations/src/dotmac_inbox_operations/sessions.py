"""Sign-out: stop dispatch now, settle the work already held on a policy.

These two halves are deliberately separated in time.

Ending a session stops NEW work in the same transaction — an agent who has gone
cannot be pushed another conversation. But the three or four conversations they
were already holding are a different problem: dropping them instantly loses
context the customer is mid-sentence in, and holding them forever means nobody
answers. So each one becomes a durable `InboxOfflineDisposition` with a due
time, and `settle_offline_dispositions` acts when the grace period expires —
unless the agent came back available first, which cancels it.

The disposition vocabulary has no `TRANSFER` member. A transfer needs a named
target and an accountable actor, and a policy can invent neither; `ESCALATE`
raises the supervisor alert that puts a human in front of that choice, which is
the honest automated step.

Rows, not scheduler memory: a queue of pending decisions held in a worker
process is lost on the next deploy, which is the same failure the round-robin
cursor was made durable to fix.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_inbox_operations.contracts import (
    DISPATCHABLE_PRESENCE_STATES,
    AgentSessionEnded,
    AssignmentStatus,
    Conflict,
    DispositionStatus,
    EndAgentSession,
    EscalateConversation,
    EscalationRequested,
    OfflineDisposition,
    OfflineDispositionsSettled,
    RequeueConversation,
    SettleOfflineDispositions,
)
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxAgentPresence,
    InboxOfflineDisposition,
    InboxWorkflowEvent,
)
from dotmac_inbox_operations.presence import go_offline
from dotmac_inbox_operations.transfers import (
    escalate_conversation,
    requeue_conversation,
)
from dotmac_inbox_operations.validation import (
    aware,
    required_text,
    tenant_of,
    utc_instant,
)

# The severity a disposition asks for when the product named none. A string,
# not a declared vocabulary: `dotmac-operational-escalations` owns severity
# terms, and naming them twice is how the two drift.
DEFAULT_ABSENCE_SEVERITY: str = "HIGH"

# The reference recorded as the actor when the policy — not a person — moves a
# conversation. It is a literal so an auditor reading `requeued by` never has to
# guess whether a human did it.
POLICY_ACTOR: str = "system:offline-policy"


def end_agent_session(
    db: Session, *, scope: TenantScope, command: EndAgentSession
) -> AgentSessionEnded:
    """Sign-out or forced offline. Dispatch stops in THIS transaction.

    Re-running it is safe: an assignment that already has a pending disposition
    is left alone rather than queued twice, so a double sign-out or a retried
    request cannot schedule the same conversation for requeue two ways.
    """
    tenant_id = tenant_of(scope)
    agent = required_text(command.agent_reference, "agent reference")
    occurred_at = aware(command.occurred_at, "occurred_at")
    reason = required_text(command.reason, "session end reason")
    if command.grace_seconds < 0:
        raise ValueError("grace_seconds must not be negative")
    severity = command.escalation_severity or DEFAULT_ABSENCE_SEVERITY
    if (
        command.disposition is OfflineDisposition.ESCALATE
        and not (command.notify_reference or "").strip()
    ):
        raise ValueError("an escalating offline policy must name who to alert")

    presence = go_offline(
        db,
        tenant_id=tenant_id,
        agent_reference=agent,
        occurred_at=occurred_at,
        reason=reason,
        actor_reference=command.actor_reference,
    )
    due_at = occurred_at + timedelta(seconds=command.grace_seconds)
    held = list(
        db.scalars(
            select(ConversationAssignment)
            .where(
                ConversationAssignment.tenant_id == tenant_id,
                ConversationAssignment.agent_reference == agent,
                ConversationAssignment.status == AssignmentStatus.ASSIGNED,
            )
            .order_by(ConversationAssignment.assigned_at, ConversationAssignment.id)
            .with_for_update()
        )
    )
    pending = set(
        db.scalars(
            select(InboxOfflineDisposition.assignment_id).where(
                InboxOfflineDisposition.tenant_id == tenant_id,
                InboxOfflineDisposition.status == DispositionStatus.PENDING,
            )
        )
    )
    created: list[UUID] = []
    for assignment in held:
        if assignment.id in pending:
            continue
        row = InboxOfflineDisposition(
            tenant_id=tenant_id,
            agent_reference=agent,
            assignment_id=assignment.id,
            conversation_reference=assignment.conversation_reference,
            disposition=command.disposition,
            status=DispositionStatus.PENDING,
            reason=reason,
            notify_reference=command.notify_reference,
            escalation_severity=severity,
            due_at=due_at,
        )
        db.add(row)
        db.flush()
        db.add(
            InboxWorkflowEvent(
                tenant_id=tenant_id,
                assignment_id=assignment.id,
                event_type="OFFLINE_HELD",
                occurred_at=occurred_at,
                reason=reason,
                actor_reference=command.actor_reference or agent,
            )
        )
        created.append(row.id)
    db.flush()
    return AgentSessionEnded(
        agent_reference=agent,
        state=presence.state,
        disposition_ids=tuple(created),
        due_at=due_at if created else None,
    )


def _agent_returned(
    db: Session,
    *,
    tenant_id: UUID,
    agent_reference: str,
    presence_fresh_after: datetime,
) -> bool:
    """Did the agent come back before the grace period ran out?

    Freshness matters as much as state here: a presence row still reading
    AVAILABLE from an hour ago is not somebody who came back, it is somebody
    whose browser died before it could say otherwise.
    """
    row = db.scalar(
        select(InboxAgentPresence).where(
            InboxAgentPresence.tenant_id == tenant_id,
            InboxAgentPresence.agent_reference == agent_reference,
        )
    )
    if row is None or row.state not in DISPATCHABLE_PRESENCE_STATES:
        return False
    return utc_instant(row.observed_at) >= utc_instant(presence_fresh_after)


def settle_offline_dispositions(
    db: Session, *, scope: TenantScope, command: SettleOfflineDispositions
) -> OfflineDispositionsSettled:
    """Apply every disposition whose grace period has expired.

    Two things cancel a disposition instead of executing it: the conversation is
    no longer held at all (someone already dealt with it), or the agent is back
    and dispatchable. Neither is a failure — both mean the reason for the policy
    went away, and executing it anyway would take work off an agent who is
    sitting there able to do it.
    """
    tenant_id = tenant_of(scope)
    settled_at = aware(command.settled_at, "settled_at")
    fresh_after = aware(command.presence_fresh_after, "presence_fresh_after")
    if command.limit < 1:
        raise ValueError("limit must be positive")
    due = list(
        db.scalars(
            select(InboxOfflineDisposition)
            .where(
                InboxOfflineDisposition.tenant_id == tenant_id,
                InboxOfflineDisposition.status == DispositionStatus.PENDING,
                InboxOfflineDisposition.due_at <= settled_at,
            )
            .order_by(InboxOfflineDisposition.due_at, InboxOfflineDisposition.id)
            .limit(command.limit)
            .with_for_update(skip_locked=True)
        )
    )
    requeued: list[UUID] = []
    escalated: list[UUID] = []
    requests: list[EscalationRequested] = []
    retained: list[UUID] = []
    cancelled: list[UUID] = []

    for row in due:
        # Locked and tenant-scoped, not `db.get`: an unlocked read here lets a
        # legitimate concurrent transfer move the conversation after the check,
        # after which this sweep would requeue the NEW holder's live work.
        assignment = db.scalar(
            select(ConversationAssignment)
            .where(
                ConversationAssignment.tenant_id == tenant_id,
                ConversationAssignment.id == row.assignment_id,
            )
            .with_for_update()
        )
        if assignment is None or assignment.status != AssignmentStatus.ASSIGNED:
            _cancel(row, settled_at, "the conversation is no longer held")
            cancelled.append(row.id)
            continue
        if _agent_returned(
            db,
            tenant_id=tenant_id,
            agent_reference=row.agent_reference,
            presence_fresh_after=fresh_after,
        ):
            _cancel(row, settled_at, "the agent returned within the grace period")
            cancelled.append(row.id)
            continue
        if row.disposition is OfflineDisposition.REQUEUE:
            requeue_conversation(
                db,
                scope=scope,
                command=RequeueConversation(
                    conversation_reference=row.conversation_reference,
                    reason=row.reason,
                    actor_reference=POLICY_ACTOR,
                    requeued_at=settled_at,
                    # The policy is not the holder, and the assignment it
                    # decided about must still be the live one.
                    expected_assignment_id=assignment.id,
                    supervisor_override=True,
                    override_reason="offline grace period expired",
                ),
            )
            _settle(row, settled_at, "requeued after the grace period expired")
            requeued.append(row.id)
        elif row.disposition is OfflineDisposition.ESCALATE:
            if row.notify_reference is None:  # pragma: no cover - write-time invariant
                raise Conflict("an escalating disposition has no alert target")
            requested = escalate_conversation(
                db,
                scope=scope,
                command=EscalateConversation(
                    conversation_reference=row.conversation_reference,
                    severity=row.escalation_severity or DEFAULT_ABSENCE_SEVERITY,
                    reason=row.reason,
                    actor_reference=POLICY_ACTOR,
                    notify_reference=row.notify_reference,
                    requested_at=settled_at,
                    due_at=settled_at,
                    # Derived from the disposition id, so a re-run of the sweep
                    # asks for the same escalation rather than a second one.
                    dedup_key=f"inbox-offline-disposition:{row.id}",
                ),
            )
            requests.append(requested)
            _settle(row, settled_at, "escalation requested for an absent agent")
            escalated.append(row.id)
        else:
            _settle(row, settled_at, "retained with the absent agent")
            retained.append(row.id)
    db.flush()
    return OfflineDispositionsSettled(
        requeued=tuple(requeued),
        escalated=tuple(escalated),
        retained=tuple(retained),
        cancelled=tuple(cancelled),
        escalation_requests=tuple(requests),
    )


def _settle(row: InboxOfflineDisposition, settled_at: datetime, note: str) -> None:
    row.status = DispositionStatus.SETTLED
    row.settled_at = settled_at
    row.settlement_note = note


def _cancel(row: InboxOfflineDisposition, settled_at: datetime, note: str) -> None:
    row.status = DispositionStatus.CANCELLED
    row.settled_at = settled_at
    row.settlement_note = note


__all__ = [
    "DEFAULT_ABSENCE_SEVERITY",
    "POLICY_ACTOR",
    "end_agent_session",
    "settle_offline_dispositions",
]
