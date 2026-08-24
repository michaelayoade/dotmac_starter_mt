"""Agent availability: what an agent chose, how fresh it is, and what follows.

Three facts are kept apart here on purpose, because every inbox in the fleet
has at some point collapsed two of them and got a wrong answer:

* **Authentication** is not availability. Signing in produces no row in this
  module. An agent becomes AVAILABLE by choosing it and no other way.
* **Freshness** is not choice. `record_presence_heartbeat` refreshes an
  already-chosen state and carries no state field at all, so a browser tab
  cannot make anyone available and a reconnect cannot end a break.
* **Busy** is not a state. It is derived from active assignments against
  capacity every time it is asked, so it is never selectable and never stale.

Dispatch eligibility is stated once, by `DISPATCHABLE_PRESENCE_STATES`, and
covers AVAILABLE only. AWAY and ON_BREAK stop NEW work while leaving work the
agent already holds assigned to them — see ADR-0059 for why the CRM behaviour
of routing to away agents was not carried forward.

Callers own authorization and transactions. This module writes evidence for a
manager override; it never decides that the actor is a manager.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_inbox_operations.contracts import (
    DISPATCHABLE_PRESENCE_STATES,
    AgentAvailability,
    AssignmentStatus,
    Conflict,
    OverrideAgentPresence,
    PresenceSource,
    PresenceState,
    RecordPresenceHeartbeat,
    SetAgentPresence,
)
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxAgentPresence,
    InboxPresenceEvent,
)
from dotmac_inbox_operations.validation import (
    aware,
    eligible_references,
    required_text,
    tenant_of,
    utc_instant,
)


def _presence(
    db: Session, tenant_id: UUID, agent_reference: str, *, lock: bool
) -> InboxAgentPresence | None:
    statement = select(InboxAgentPresence).where(
        InboxAgentPresence.tenant_id == tenant_id,
        InboxAgentPresence.agent_reference == agent_reference,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _record_transition(
    db: Session,
    *,
    tenant_id: UUID,
    agent_reference: str,
    previous_state: PresenceState | None,
    previous_capacity: int | None,
    state: PresenceState,
    assignment_capacity: int,
    source: PresenceSource,
    occurred_at: datetime,
    actor_reference: str | None = None,
    reason: str | None = None,
) -> InboxPresenceEvent | None:
    """Append evidence for a real CHANGE only.

    A heartbeat that changes nothing writes no row. Thirty-second beats would
    otherwise bury the handful of rows that answer "who put this agent on
    break, and why" under thousands that say nothing happened.
    """
    if previous_state == state and previous_capacity == assignment_capacity:
        return None
    event = InboxPresenceEvent(
        tenant_id=tenant_id,
        agent_reference=agent_reference,
        previous_state=previous_state,
        previous_capacity=previous_capacity,
        state=state,
        assignment_capacity=assignment_capacity,
        source=source,
        actor_reference=actor_reference,
        reason=reason,
        occurred_at=occurred_at,
    )
    db.add(event)
    db.flush()
    return event


def _require_self(agent: str, actor: str, what: str) -> None:
    """A permission proves the actor may perform the operation, never on whom.

    `inbox_operations.presence.self` is the one code an ordinary agent holds.
    If these commands accepted any `agent_reference`, that code would silently
    also be `presence.manage` — and a manage-shaped change would arrive with no
    reason attached, which is the whole thing the override command exists to
    capture.
    """
    if agent != actor:
        raise Conflict(f"{what} may only act on the acting agent")


def set_agent_presence(
    db: Session, *, scope: TenantScope, command: SetAgentPresence
) -> InboxAgentPresence:
    """The agent's own explicit choice of state and capacity.

    Last-observation-wins on `observed_at`, so a slow request cannot overwrite a
    newer choice with an older one.
    """
    tenant_id = tenant_of(scope)
    agent = required_text(command.agent_reference, "agent reference")
    _require_self(
        agent,
        required_text(command.actor_reference, "actor reference"),
        "choosing presence",
    )
    observed_at = aware(command.observed_at, "observed_at")
    if command.assignment_capacity < 0:
        raise Conflict("assignment capacity must not be negative")
    row = _presence(db, tenant_id, agent, lock=True)
    if row is None:
        candidate = InboxAgentPresence(
            tenant_id=tenant_id,
            agent_reference=agent,
            state=command.state,
            assignment_capacity=command.assignment_capacity,
            observed_at=observed_at,
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(candidate)
                db.flush()
        except IntegrityError as exc:
            row = _presence(db, tenant_id, agent, lock=True)
            if row is None:  # pragma: no cover - database invariant defense
                raise Conflict("agent presence conflicted outside the tenant") from exc
        else:
            _record_transition(
                db,
                tenant_id=tenant_id,
                agent_reference=agent,
                previous_state=None,
                previous_capacity=None,
                state=command.state,
                assignment_capacity=command.assignment_capacity,
                source=PresenceSource.AGENT,
                occurred_at=observed_at,
            )
            return candidate
    if utc_instant(observed_at) >= utc_instant(row.observed_at):
        previous_state, previous_capacity = row.state, row.assignment_capacity
        row.state = command.state
        row.assignment_capacity = command.assignment_capacity
        row.observed_at = observed_at
        _record_transition(
            db,
            tenant_id=tenant_id,
            agent_reference=agent,
            previous_state=previous_state,
            previous_capacity=previous_capacity,
            state=command.state,
            assignment_capacity=command.assignment_capacity,
            source=PresenceSource.AGENT,
            occurred_at=observed_at,
        )
    db.flush()
    return row


def record_presence_heartbeat(
    db: Session, *, scope: TenantScope, command: RecordPresenceHeartbeat
) -> InboxAgentPresence:
    """Keep a chosen state fresh. It cannot choose, and it cannot resurrect.

    An OFFLINE agent's heartbeat is refused rather than ignored. A tab left open
    after sign-out would otherwise keep producing evidence of a person who is
    gone, and the whole reason offline stops dispatch immediately is that nobody
    is there to answer.
    """
    tenant_id = tenant_of(scope)
    agent = required_text(command.agent_reference, "agent reference")
    _require_self(
        agent,
        required_text(command.actor_reference, "actor reference"),
        "a heartbeat",
    )
    observed_at = aware(command.observed_at, "observed_at")
    row = _presence(db, tenant_id, agent, lock=True)
    if row is None:
        raise Conflict("agent has no chosen presence for a heartbeat to refresh")
    if row.state == PresenceState.OFFLINE:
        raise Conflict("an offline agent's heartbeat cannot restore presence")
    if utc_instant(observed_at) > utc_instant(row.observed_at):
        row.observed_at = observed_at
        db.flush()
    return row


def override_agent_presence(
    db: Session, *, scope: TenantScope, command: OverrideAgentPresence
) -> InboxAgentPresence:
    """A manager changes another agent's state or capacity, with a reason.

    The reason is enforced here, not left to the caller, because an override
    with no recorded reason is afterwards indistinguishable from the agent's own
    choice — which is exactly the question a supervisor review has to answer.
    """
    tenant_id = tenant_of(scope)
    agent = required_text(command.agent_reference, "agent reference")
    actor = required_text(command.actor_reference, "override actor reference")
    reason = required_text(command.reason, "override reason")
    observed_at = aware(command.observed_at, "observed_at")
    if command.state is None and command.assignment_capacity is None:
        raise ValueError("an override must change state, capacity, or both")
    if command.assignment_capacity is not None and command.assignment_capacity < 0:
        raise Conflict("assignment capacity must not be negative")
    row = _presence(db, tenant_id, agent, lock=True)
    if row is None:
        raise Conflict("agent has no presence record to override")
    if utc_instant(observed_at) < utc_instant(row.observed_at):
        raise Conflict("an override cannot predate the presence it replaces")
    previous_state, previous_capacity = row.state, row.assignment_capacity
    row.state = command.state if command.state is not None else row.state
    row.assignment_capacity = (
        command.assignment_capacity
        if command.assignment_capacity is not None
        else row.assignment_capacity
    )
    row.observed_at = observed_at
    _record_transition(
        db,
        tenant_id=tenant_id,
        agent_reference=agent,
        previous_state=previous_state,
        previous_capacity=previous_capacity,
        state=row.state,
        assignment_capacity=row.assignment_capacity,
        source=PresenceSource.MANAGER,
        occurred_at=observed_at,
        actor_reference=actor,
        reason=reason,
    )
    db.flush()
    return row


def go_offline(
    db: Session,
    *,
    tenant_id: UUID,
    agent_reference: str,
    occurred_at: datetime,
    reason: str,
    actor_reference: str | None,
) -> InboxAgentPresence:
    """Force OFFLINE as a session consequence, bypassing observation ordering.

    Sign-out is not an observation competing with other observations: it is the
    end of the session that produced them. It therefore wins regardless of
    `observed_at` ordering, which is what makes "logout stops dispatch
    immediately" true even when a heartbeat is in flight behind it.
    """
    row = _presence(db, tenant_id, agent_reference, lock=True)
    if row is None:
        # Signing in deliberately creates no presence, so an agent who never
        # chose a state has no row — and refusing here would mean their
        # sign-out could not be recorded and any supervisor-assigned work they
        # hold would get no disposition. OFFLINE with zero capacity is the
        # truthful row to write, not an error.
        row = InboxAgentPresence(
            tenant_id=tenant_id,
            agent_reference=agent_reference,
            state=PresenceState.OFFLINE,
            assignment_capacity=0,
            observed_at=occurred_at,
        )
        db.add(row)
        db.flush()
        _record_transition(
            db,
            tenant_id=tenant_id,
            agent_reference=agent_reference,
            previous_state=None,
            previous_capacity=None,
            state=PresenceState.OFFLINE,
            assignment_capacity=0,
            source=PresenceSource.SESSION,
            occurred_at=occurred_at,
            actor_reference=actor_reference,
            reason=reason,
        )
        return row
    previous_state, previous_capacity = row.state, row.assignment_capacity
    row.state = PresenceState.OFFLINE
    row.observed_at = occurred_at
    _record_transition(
        db,
        tenant_id=tenant_id,
        agent_reference=agent_reference,
        previous_state=previous_state,
        previous_capacity=previous_capacity,
        state=PresenceState.OFFLINE,
        assignment_capacity=row.assignment_capacity,
        source=PresenceSource.SESSION,
        occurred_at=occurred_at,
        actor_reference=actor_reference,
        reason=reason,
    )
    db.flush()
    return row


def active_assignment_counts(
    db: Session, *, tenant_id: UUID, agent_references: tuple[str, ...]
) -> dict[str, int]:
    """Held-conversation load per agent — the numerator of `busy`."""
    if not agent_references:
        return {}
    rows = db.execute(
        select(
            ConversationAssignment.agent_reference,
            func.count(ConversationAssignment.id),
        )
        .where(
            ConversationAssignment.tenant_id == tenant_id,
            ConversationAssignment.agent_reference.in_(agent_references),
            ConversationAssignment.status == AssignmentStatus.ASSIGNED,
        )
        .group_by(ConversationAssignment.agent_reference)
    )
    counts: dict[str, int] = {}
    for agent_reference, count in rows:
        counts[agent_reference] = count
    return counts


def agent_availability(
    db: Session,
    *,
    scope: TenantScope,
    agent_reference: str,
    presence_fresh_after: datetime,
) -> AgentAvailability:
    """Derive — never read — whether this agent can take another conversation."""
    tenant_id = tenant_of(scope)
    agent = required_text(agent_reference, "agent reference")
    fresh_after = aware(presence_fresh_after, "presence_fresh_after")
    row = _presence(db, tenant_id, agent, lock=False)
    if row is None:
        raise Conflict("agent has no presence record in the tenant")
    active = active_assignment_counts(
        db, tenant_id=tenant_id, agent_references=(agent,)
    ).get(agent, 0)
    fresh = utc_instant(row.observed_at) >= utc_instant(fresh_after)
    busy = active >= row.assignment_capacity
    return AgentAvailability(
        agent_reference=agent,
        state=row.state,
        assignment_capacity=row.assignment_capacity,
        active_assignments=active,
        observed_at=row.observed_at,
        presence_fresh=fresh,
        busy=busy,
        dispatchable=(row.state in DISPATCHABLE_PRESENCE_STATES and fresh and not busy),
    )


def available_agents(
    db: Session,
    *,
    tenant_id: UUID,
    eligible_agent_references: tuple[str, ...],
    presence_fresh_after: datetime,
    lock: bool,
) -> list[str]:
    """The dispatchable subset of an eligible cohort, in a stable order.

    The state filter reads `DISPATCHABLE_PRESENCE_STATES` rather than naming
    AVAILABLE inline, so there is exactly one place in the package where "who
    may receive new work" can be changed or reviewed.
    """
    eligible = eligible_references(eligible_agent_references)
    fresh_after = aware(presence_fresh_after, "presence_fresh_after")
    statement = (
        select(InboxAgentPresence)
        .where(
            InboxAgentPresence.tenant_id == tenant_id,
            InboxAgentPresence.agent_reference.in_(eligible),
            InboxAgentPresence.state.in_(tuple(DISPATCHABLE_PRESENCE_STATES)),
            InboxAgentPresence.observed_at >= fresh_after,
            InboxAgentPresence.assignment_capacity > 0,
        )
        .order_by(InboxAgentPresence.agent_reference)
    )
    if lock:
        statement = statement.with_for_update()
    presences = list(db.scalars(statement))
    if not presences:
        return []
    counts = active_assignment_counts(
        db,
        tenant_id=tenant_id,
        agent_references=tuple(row.agent_reference for row in presences),
    )
    return [
        row.agent_reference
        for row in presences
        if counts.get(row.agent_reference, 0) < row.assignment_capacity
    ]


def refuse_undispatchable(
    db: Session,
    *,
    tenant_id: UUID,
    agent_reference: str,
    presence_fresh_after: datetime,
) -> None:
    """Raise the specific reason an agent cannot take new work.

    Callers that already know the agent is not in the available set use this to
    turn "not available" into the operator-facing distinction between stale
    presence, a paused agent and a full one.
    """
    row = _presence(db, tenant_id, agent_reference, lock=False)
    if row is None or utc_instant(row.observed_at) < utc_instant(presence_fresh_after):
        raise Conflict("agent presence is missing or not fresh")
    if row.state not in DISPATCHABLE_PRESENCE_STATES:
        raise Conflict("agent is not available for inbox assignment")
    raise Conflict("agent assignment capacity is exhausted")


__all__ = [
    "active_assignment_counts",
    "agent_availability",
    "available_agents",
    "go_offline",
    "override_agent_presence",
    "record_presence_heartbeat",
    "refuse_undispatchable",
    "set_agent_presence",
]
