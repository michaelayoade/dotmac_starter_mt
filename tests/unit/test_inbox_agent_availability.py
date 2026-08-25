"""Availability, ownership movement and escalation as SEPARATE decisions.

Every test here is a claim about a distinction the fleet currently blurs:
authentication versus availability, freshness versus choice, busy as derived
rather than selected, and the five different things Sub's one "Escalate to
teammate" button did.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_inbox_operations.contracts import (
    AcceptTransfer,
    AdmitToQueue,
    AssignConversation,
    AssignmentStatus,
    CancelTransfer,
    ClaimConversation,
    Conflict,
    CreateQueue,
    DeclineTransfer,
    DispositionStatus,
    EndAgentSession,
    EscalateConversation,
    ExpireTransferRequests,
    OfflineDisposition,
    OverrideAgentPresence,
    PresenceSource,
    PresenceState,
    PromoteFromQueue,
    RecordPresenceHeartbeat,
    RequestTransfer,
    RequeueConversation,
    SetAgentPresence,
    SettleOfflineDispositions,
    TransferConversation,
    TransferKind,
    TransferStatus,
)
from dotmac_inbox_operations.models import (
    TENANT_TABLES,
    ConversationAssignment,
    InboxEscalationRequest,
    InboxOfflineDisposition,
    InboxPresenceEvent,
    InboxQueueEntry,
    InboxTransferRequest,
    InboxWorkflowEvent,
)
from dotmac_inbox_operations.presence import (
    agent_availability,
    override_agent_presence,
    record_presence_heartbeat,
)
from dotmac_inbox_operations.service import (
    admit_to_queue,
    assign_conversation,
    create_queue,
    promote_from_queue,
    set_agent_presence,
)
from dotmac_inbox_operations.sessions import (
    POLICY_ACTOR,
    end_agent_session,
    settle_offline_dispositions,
)
from dotmac_inbox_operations.transfers import (
    accept_transfer,
    cancel_transfer,
    claim_conversation,
    decline_transfer,
    escalate_conversation,
    expire_transfer_requests,
    request_transfer,
    requeue_conversation,
    transfer_conversation,
)
from dotmac_inbox_operations.validation import utc_instant
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
FRESH_AFTER = NOW - timedelta(minutes=5)
SCOPE = TenantScope(TENANT_A)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_inbox_ops": None}},
    )
    Tenant.__table__.create(engine)
    # The transfer and escalation commands enqueue the alerts they promise, so
    # the outbox is part of the transaction under test, not scenery.
    OutboxEvent.__table__.create(engine)
    from dotmac_inbox_operations import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_A, slug="a", name="A"))
        session.flush()
        yield session
    engine.dispose()


def _available(
    db: Session, agent: str, *, capacity: int = 2, at: datetime = NOW
) -> None:
    set_agent_presence(
        db,
        scope=SCOPE,
        command=SetAgentPresence(
            agent_reference=agent,
            state=PresenceState.AVAILABLE,
            assignment_capacity=capacity,
            observed_at=at,
            actor_reference=agent,
        ),
    )


def _queue(db: Session, code: str = "support"):
    return create_queue(db, scope=SCOPE, command=CreateQueue(code, code.title()))


def _assigned(db: Session, queue, conversation: str, agent: str):
    return assign_conversation(
        db,
        scope=SCOPE,
        command=AssignConversation(
            conversation_reference=conversation,
            queue_id=queue.id,
            agent_reference=agent,
            assigned_at=NOW,
            eligible_agent_references=(agent,),
            presence_fresh_after=FRESH_AFTER,
        ),
    )


# ── Authentication is not availability ──────────────────────────────────────


def test_a_heartbeat_cannot_create_availability_out_of_nothing(db: Session) -> None:
    """Signing in produces no presence. A browser that starts beating for an
    agent who never chose a state must not manufacture one."""
    with pytest.raises(Conflict, match="no chosen presence"):
        record_presence_heartbeat(
            db,
            scope=SCOPE,
            command=RecordPresenceHeartbeat(
                agent_reference="ann", observed_at=NOW, actor_reference="ann"
            ),
        )


def test_a_heartbeat_refreshes_freshness_and_changes_nothing_else(
    db: Session,
) -> None:
    set_agent_presence(
        db,
        scope=SCOPE,
        command=SetAgentPresence("ann", PresenceState.ON_BREAK, 3, NOW, "ann"),
    )
    later = NOW + timedelta(minutes=2)
    row = record_presence_heartbeat(
        db,
        scope=SCOPE,
        command=RecordPresenceHeartbeat(
            agent_reference="ann", observed_at=later, actor_reference="ann"
        ),
    )
    assert row.state is PresenceState.ON_BREAK
    assert row.assignment_capacity == 3
    assert utc_instant(row.observed_at) == later
    # Transitions only: the beat itself is not an event worth keeping.
    assert db.scalars(select(InboxPresenceEvent)).all().__len__() == 1


def test_an_offline_agents_heartbeat_is_refused(db: Session) -> None:
    """A tab left open after sign-out must not keep producing evidence of a
    person who has gone home."""
    set_agent_presence(
        db,
        scope=SCOPE,
        command=SetAgentPresence("ann", PresenceState.OFFLINE, 2, NOW, "ann"),
    )
    with pytest.raises(Conflict, match="cannot restore presence"):
        record_presence_heartbeat(
            db,
            scope=SCOPE,
            command=RecordPresenceHeartbeat(
                agent_reference="ann",
                observed_at=NOW + timedelta(minutes=1),
                actor_reference="ann",
            ),
        )


# ── Busy is derived ─────────────────────────────────────────────────────────


def test_busy_follows_the_work_and_is_never_chosen(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann", capacity=1)
    before = agent_availability(
        db, scope=SCOPE, agent_reference="ann", presence_fresh_after=FRESH_AFTER
    )
    assert (before.busy, before.dispatchable, before.active_assignments) == (
        False,
        True,
        0,
    )

    _assigned(db, queue, "conv-1", "ann")
    after = agent_availability(
        db, scope=SCOPE, agent_reference="ann", presence_fresh_after=FRESH_AFTER
    )
    # The agent never said a word; taking one conversation at capacity one is
    # what made them busy, and finishing it is what will end it.
    assert after.state is PresenceState.AVAILABLE
    assert (after.busy, after.dispatchable, after.active_assignments) == (
        True,
        False,
        1,
    )


def test_stale_presence_is_not_dispatchable_however_available_it_claims(
    db: Session,
) -> None:
    _available(db, "ann", at=NOW - timedelta(hours=2))
    view = agent_availability(
        db, scope=SCOPE, agent_reference="ann", presence_fresh_after=FRESH_AFTER
    )
    assert view.state is PresenceState.AVAILABLE
    assert (view.presence_fresh, view.dispatchable) == (False, False)


# ── Paused states stop new work without taking away held work ───────────────


@pytest.mark.parametrize("paused", [PresenceState.AWAY, PresenceState.ON_BREAK])
def test_away_and_on_break_stop_dispatch_but_keep_existing_chats(
    db: Session, paused: PresenceState
) -> None:
    """The CRM/Sub conflict, resolved: a paused agent receives nothing new, and
    loses nothing they are already holding."""
    queue = _queue(db)
    _available(db, "ann", capacity=3)
    held = _assigned(db, queue, "conv-held", "ann")

    set_agent_presence(
        db,
        scope=SCOPE,
        command=SetAgentPresence("ann", paused, 3, NOW + timedelta(seconds=1), "ann"),
    )
    admit_to_queue(
        db,
        scope=SCOPE,
        command=AdmitToQueue(queue_id=queue.id, conversation_reference="conv-new"),
    )
    with pytest.raises(Conflict, match="no work that an eligible available agent"):
        promote_from_queue(
            db,
            scope=SCOPE,
            command=PromoteFromQueue(
                queue_id=queue.id,
                eligible_agent_references=("ann",),
                presence_fresh_after=FRESH_AFTER,
                promoted_at=NOW + timedelta(minutes=1),
            ),
        )
    db.refresh(held)
    assert held.status is AssignmentStatus.ASSIGNED


# ── Manager override ────────────────────────────────────────────────────────


def test_a_manager_override_records_who_and_why(db: Session) -> None:
    _available(db, "ann", capacity=4)
    row = override_agent_presence(
        db,
        scope=SCOPE,
        command=OverrideAgentPresence(
            agent_reference="ann",
            actor_reference="sam",
            reason="covering the evening escalation desk",
            observed_at=NOW + timedelta(minutes=1),
            state=PresenceState.AWAY,
            assignment_capacity=1,
        ),
    )
    assert (row.state, row.assignment_capacity) == (PresenceState.AWAY, 1)
    event = db.scalars(
        select(InboxPresenceEvent)
        .where(InboxPresenceEvent.source == PresenceSource.MANAGER)
        .order_by(InboxPresenceEvent.occurred_at)
    ).one()
    assert event.actor_reference == "sam"
    assert event.reason == "covering the evening escalation desk"
    assert event.previous_state is PresenceState.AVAILABLE
    assert (event.previous_capacity, event.assignment_capacity) == (4, 1)


@pytest.mark.parametrize("field", ["actor_reference", "reason"])
def test_an_override_without_its_evidence_is_refused(db: Session, field: str) -> None:
    _available(db, "ann")
    kwargs = {
        "agent_reference": "ann",
        "actor_reference": "sam",
        "reason": "capacity review",
        "observed_at": NOW,
        "assignment_capacity": 1,
    }
    kwargs[field] = "   "
    with pytest.raises(ValueError, match="must not be empty"):
        override_agent_presence(
            db, scope=SCOPE, command=OverrideAgentPresence(**kwargs)
        )


def test_an_override_that_changes_nothing_is_a_programming_error(db: Session) -> None:
    _available(db, "ann")
    with pytest.raises(ValueError, match="state, capacity, or both"):
        override_agent_presence(
            db,
            scope=SCOPE,
            command=OverrideAgentPresence(
                agent_reference="ann",
                actor_reference="sam",
                reason="none",
                observed_at=NOW,
            ),
        )


# ── Sign-out ────────────────────────────────────────────────────────────────


def test_signing_out_stops_dispatch_in_the_same_transaction(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann", capacity=3)
    admit_to_queue(
        db,
        scope=SCOPE,
        command=AdmitToQueue(queue_id=queue.id, conversation_reference="conv-waiting"),
    )
    end_agent_session(
        db,
        scope=SCOPE,
        command=EndAgentSession(
            agent_reference="ann",
            occurred_at=NOW + timedelta(minutes=1),
            disposition=OfflineDisposition.RETAIN,
            grace_seconds=300,
            reason="end of shift",
        ),
    )
    with pytest.raises(Conflict):
        promote_from_queue(
            db,
            scope=SCOPE,
            command=PromoteFromQueue(
                queue_id=queue.id,
                eligible_agent_references=("ann",),
                presence_fresh_after=FRESH_AFTER,
                promoted_at=NOW + timedelta(minutes=2),
            ),
        )


def test_sign_out_wins_over_a_heartbeat_still_in_flight(db: Session) -> None:
    """A beat stamped a second earlier must not undo the sign-out that follows
    it. Session end is the end of the session, not a competing observation."""
    _available(db, "ann")
    end_agent_session(
        db,
        scope=SCOPE,
        command=EndAgentSession(
            agent_reference="ann",
            occurred_at=NOW - timedelta(seconds=1),
            disposition=OfflineDisposition.RETAIN,
            grace_seconds=0,
            reason="logout",
        ),
    )
    view = agent_availability(
        db, scope=SCOPE, agent_reference="ann", presence_fresh_after=FRESH_AFTER
    )
    assert view.state is PresenceState.OFFLINE
    assert view.dispatchable is False


def test_held_conversations_wait_out_the_configured_grace_period(
    db: Session,
) -> None:
    queue = _queue(db)
    _available(db, "ann", capacity=2)
    held = _assigned(db, queue, "conv-1", "ann")
    ended = end_agent_session(
        db,
        scope=SCOPE,
        command=EndAgentSession(
            agent_reference="ann",
            occurred_at=NOW,
            disposition=OfflineDisposition.REQUEUE,
            grace_seconds=600,
            reason="browser closed",
        ),
    )
    assert len(ended.disposition_ids) == 1
    assert ended.due_at == NOW + timedelta(seconds=600)
    db.refresh(held)
    assert held.status is AssignmentStatus.ASSIGNED

    # Too early: the grace period is the point.
    early = settle_offline_dispositions(
        db,
        scope=SCOPE,
        command=SettleOfflineDispositions(
            settled_at=NOW + timedelta(seconds=60), presence_fresh_after=FRESH_AFTER
        ),
    )
    assert early == early.__class__((), (), (), ())

    settled_at = NOW + timedelta(seconds=601)
    result = settle_offline_dispositions(
        db,
        scope=SCOPE,
        command=SettleOfflineDispositions(
            settled_at=settled_at,
            presence_fresh_after=settled_at - timedelta(minutes=5),
        ),
    )
    assert result.requeued == ended.disposition_ids
    disposition = db.scalars(select(InboxOfflineDisposition)).one()
    assert disposition.status is DispositionStatus.SETTLED
    db.refresh(held)
    assert held.status is AssignmentStatus.REQUEUED
    entry = db.scalars(
        select(InboxQueueEntry).where(
            InboxQueueEntry.conversation_reference == "conv-1"
        )
    ).one()
    assert entry.status.value == "QUEUED"


def test_an_agent_who_comes_back_cancels_their_own_disposition(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann", capacity=2)
    held = _assigned(db, queue, "conv-1", "ann")
    end_agent_session(
        db,
        scope=SCOPE,
        command=EndAgentSession(
            agent_reference="ann",
            occurred_at=NOW,
            disposition=OfflineDisposition.REQUEUE,
            grace_seconds=60,
            reason="connection dropped",
        ),
    )
    back_at = NOW + timedelta(seconds=30)
    _available(db, "ann", capacity=2, at=back_at)

    settled_at = NOW + timedelta(seconds=90)
    result = settle_offline_dispositions(
        db,
        scope=SCOPE,
        command=SettleOfflineDispositions(
            settled_at=settled_at,
            presence_fresh_after=settled_at - timedelta(minutes=5),
        ),
    )
    assert len(result.cancelled) == 1 and not result.requeued
    disposition = db.scalars(select(InboxOfflineDisposition)).one()
    assert disposition.status is DispositionStatus.CANCELLED
    db.refresh(held)
    assert held.status is AssignmentStatus.ASSIGNED


def test_an_escalating_offline_policy_alerts_a_supervisor(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann", capacity=2)
    _assigned(db, queue, "conv-1", "ann")
    end_agent_session(
        db,
        scope=SCOPE,
        command=EndAgentSession(
            agent_reference="ann",
            occurred_at=NOW,
            disposition=OfflineDisposition.ESCALATE,
            grace_seconds=0,
            reason="agent vanished mid-conversation",
            notify_reference="team-lead:support",
            escalation_severity="CRITICAL",
        ),
    )
    settled_at = NOW + timedelta(seconds=1)
    result = settle_offline_dispositions(
        db,
        scope=SCOPE,
        command=SettleOfflineDispositions(
            settled_at=settled_at, presence_fresh_after=FRESH_AFTER
        ),
    )
    assert len(result.escalated) == 1
    row = db.scalars(select(InboxEscalationRequest)).one()
    assert row.severity == "CRITICAL"
    assert row.notify_reference == "team-lead:support"
    assert row.requested_by_reference == POLICY_ACTOR
    # The sweep does not deliver: it hands the product exactly what the
    # escalation owner's `raise_escalation` needs, dedup key included.
    (requested,) = result.escalation_requests
    disposition = db.scalars(select(InboxOfflineDisposition)).one()
    assert requested.dedup_key == f"inbox-offline-disposition:{disposition.id}"
    assert requested.conversation_reference == "conv-1"
    assert requested.severity == "CRITICAL"


def test_an_escalating_policy_with_nobody_to_alert_is_refused(db: Session) -> None:
    _available(db, "ann")
    with pytest.raises(ValueError, match="must name who to alert"):
        end_agent_session(
            db,
            scope=SCOPE,
            command=EndAgentSession(
                agent_reference="ann",
                occurred_at=NOW,
                disposition=OfflineDisposition.ESCALATE,
                grace_seconds=0,
                reason="gone",
            ),
        )


def test_signing_out_twice_does_not_queue_the_same_work_twice(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann", capacity=2)
    _assigned(db, queue, "conv-1", "ann")
    command = EndAgentSession(
        agent_reference="ann",
        occurred_at=NOW,
        disposition=OfflineDisposition.REQUEUE,
        grace_seconds=60,
        reason="logout",
    )
    first = end_agent_session(db, scope=SCOPE, command=command)
    second = end_agent_session(db, scope=SCOPE, command=command)
    assert len(first.disposition_ids) == 1
    assert second.disposition_ids == ()
    assert len(db.scalars(select(InboxOfflineDisposition)).all()) == 1


# ── Claim ───────────────────────────────────────────────────────────────────


def test_an_agent_claims_queued_work(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    entry = admit_to_queue(
        db,
        scope=SCOPE,
        command=AdmitToQueue(queue_id=queue.id, conversation_reference="conv-1"),
    )
    assignment = claim_conversation(
        db,
        scope=SCOPE,
        command=ClaimConversation(
            conversation_reference="conv-1",
            agent_reference="ann",
            claimed_at=NOW,
            presence_fresh_after=FRESH_AFTER,
            eligible_queue_ids=(queue.id,),
            actor_reference="ann",
        ),
    )
    assert assignment.agent_reference == "ann"
    db.refresh(entry)
    assert entry.status.value == "PROMOTED"
    event = db.scalars(
        select(InboxWorkflowEvent).where(
            InboxWorkflowEvent.assignment_id == assignment.id
        )
    ).one()
    assert event.event_type == "CLAIMED"


def test_a_claim_cannot_reach_outside_the_agents_queues(db: Session) -> None:
    support, billing = _queue(db, "support"), _queue(db, "billing")
    _available(db, "ann")
    admit_to_queue(
        db,
        scope=SCOPE,
        command=AdmitToQueue(queue_id=billing.id, conversation_reference="conv-1"),
    )
    with pytest.raises(Conflict, match="outside the agent's eligible queues"):
        claim_conversation(
            db,
            scope=SCOPE,
            command=ClaimConversation(
                conversation_reference="conv-1",
                agent_reference="ann",
                claimed_at=NOW,
                presence_fresh_after=FRESH_AFTER,
                eligible_queue_ids=(support.id,),
                actor_reference="ann",
            ),
        )


def test_a_paused_agent_cannot_claim_their_way_around_dispatch(db: Session) -> None:
    queue = _queue(db)
    set_agent_presence(
        db,
        scope=SCOPE,
        command=SetAgentPresence("ann", PresenceState.ON_BREAK, 2, NOW, "ann"),
    )
    admit_to_queue(
        db,
        scope=SCOPE,
        command=AdmitToQueue(queue_id=queue.id, conversation_reference="conv-1"),
    )
    with pytest.raises(Conflict, match="not available"):
        claim_conversation(
            db,
            scope=SCOPE,
            command=ClaimConversation(
                conversation_reference="conv-1",
                agent_reference="ann",
                claimed_at=NOW,
                presence_fresh_after=FRESH_AFTER,
                eligible_queue_ids=(queue.id,),
                actor_reference="ann",
            ),
        )


# ── Cold transfer ───────────────────────────────────────────────────────────


def test_a_cold_transfer_moves_ownership_and_records_both_sides(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    previous = _assigned(db, queue, "conv-1", "ann")

    at = NOW + timedelta(minutes=5)
    moved = transfer_conversation(
        db,
        scope=SCOPE,
        command=TransferConversation(
            conversation_reference="conv-1",
            to_agent_reference="bob",
            reason="bob owns this customer's migration",
            actor_reference="ann",
            transferred_at=at,
            presence_fresh_after=at - timedelta(minutes=5),
            eligible_agent_references=("ann", "bob"),
            notify_reference="agent:bob",
        ),
    )
    db.refresh(previous)
    assert previous.status is AssignmentStatus.TRANSFERRED
    assert utc_instant(previous.released_at) == at
    assert moved.from_agent_reference == "ann"
    assert moved.to_agent_reference == "bob"
    assert moved.notify_reference == "agent:bob"

    new = db.get(ConversationAssignment, moved.new_assignment_id)
    assert new is not None
    assert (new.agent_reference, new.status) == ("bob", AssignmentStatus.ASSIGNED)

    request = db.get(InboxTransferRequest, moved.request_id)
    assert request is not None
    assert (request.kind, request.status) == (
        TransferKind.COLD,
        TransferStatus.ACCEPTED,
    )
    assert request.reason == "bob owns this customer's migration"
    assert request.from_agent_reference == "ann"
    assert request.supervisor_override is False

    types = {row.event_type for row in db.scalars(select(InboxWorkflowEvent)).all()}
    assert {"TRANSFERRED_OUT", "TRANSFERRED_IN"} <= types


def test_a_transfer_without_a_reason_is_refused(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    with pytest.raises(ValueError, match="transfer reason must not be empty"):
        transfer_conversation(
            db,
            scope=SCOPE,
            command=TransferConversation(
                conversation_reference="conv-1",
                to_agent_reference="bob",
                reason="  ",
                actor_reference="ann",
                transferred_at=NOW,
                presence_fresh_after=FRESH_AFTER,
                eligible_agent_references=("ann", "bob"),
            ),
        )


def test_a_full_or_offline_target_needs_a_supervisor_override(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    set_agent_presence(
        db,
        scope=SCOPE,
        command=SetAgentPresence("bob", PresenceState.OFFLINE, 2, NOW, "bob"),
    )
    _assigned(db, queue, "conv-1", "ann")
    command = TransferConversation(
        conversation_reference="conv-1",
        to_agent_reference="bob",
        reason="bob has the context",
        actor_reference="ann",
        transferred_at=NOW,
        presence_fresh_after=FRESH_AFTER,
        eligible_agent_references=("ann", "bob"),
    )
    with pytest.raises(Conflict, match="not available"):
        transfer_conversation(db, scope=SCOPE, command=command)

    moved = transfer_conversation(
        db,
        scope=SCOPE,
        command=dataclasses.replace(
            command,
            supervisor_override=True,
            override_reason="bob is on the phone with the customer already",
            actor_reference="sam",
        ),
    )
    request = db.get(InboxTransferRequest, moved.request_id)
    assert request is not None
    assert request.supervisor_override is True
    assert request.override_reason


def test_a_cross_team_target_needs_a_supervisor_override(db: Session) -> None:
    support, billing = _queue(db, "support"), _queue(db, "billing")
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, support, "conv-1", "ann")
    with pytest.raises(Conflict, match="cross-team transfer requires"):
        transfer_conversation(
            db,
            scope=SCOPE,
            command=TransferConversation(
                conversation_reference="conv-1",
                to_agent_reference="bob",
                reason="billing question",
                actor_reference="ann",
                transferred_at=NOW,
                presence_fresh_after=FRESH_AFTER,
                eligible_agent_references=("ann", "bob"),
                to_queue_id=billing.id,
            ),
        )


def test_an_override_must_state_its_own_reason(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    with pytest.raises(ValueError, match="override must state its own reason"):
        transfer_conversation(
            db,
            scope=SCOPE,
            command=TransferConversation(
                conversation_reference="conv-1",
                to_agent_reference="bob",
                reason="handover",
                actor_reference="sam",
                transferred_at=NOW,
                presence_fresh_after=FRESH_AFTER,
                supervisor_override=True,
            ),
        )


# ── Warm transfer ───────────────────────────────────────────────────────────


def _warm(db: Session, queue, *, expires_in: int = 300):
    return request_transfer(
        db,
        scope=SCOPE,
        command=RequestTransfer(
            conversation_reference="conv-1",
            to_agent_reference="bob",
            reason="bob handled the last ticket for this customer",
            actor_reference="ann",
            requested_at=NOW,
            expires_at=NOW + timedelta(seconds=expires_in),
            presence_fresh_after=FRESH_AFTER,
            eligible_agent_references=("ann", "bob"),
            notify_reference="agent:bob",
        ),
    )


def test_a_warm_request_leaves_the_original_agent_responsible(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    held = _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue)
    assert request.status is TransferStatus.REQUESTED
    db.refresh(held)
    assert (held.agent_reference, held.status) == ("ann", AssignmentStatus.ASSIGNED)


def test_only_the_named_target_may_accept(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue)
    with pytest.raises(Conflict, match="only the transfer target may accept"):
        accept_transfer(
            db,
            scope=SCOPE,
            command=AcceptTransfer(
                request_id=request.id,
                actor_reference="carol",
                accepted_at=NOW + timedelta(minutes=1),
                presence_fresh_after=FRESH_AFTER,
                eligible_agent_references=("ann", "bob"),
            ),
        )


def test_accepting_moves_ownership_and_closes_the_request(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    held = _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue)
    at = NOW + timedelta(minutes=1)
    moved = accept_transfer(
        db,
        scope=SCOPE,
        command=AcceptTransfer(
            request_id=request.id,
            actor_reference="bob",
            accepted_at=at,
            presence_fresh_after=at - timedelta(minutes=5),
            eligible_agent_references=("ann", "bob"),
        ),
    )
    db.refresh(held)
    db.refresh(request)
    assert held.status is AssignmentStatus.TRANSFERRED
    assert request.status is TransferStatus.ACCEPTED
    assert request.resulting_assignment_id == moved.new_assignment_id
    assert request.settled_by_reference == "bob"


def test_declining_leaves_the_conversation_exactly_where_it_was(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    held = _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue)
    declined = decline_transfer(
        db,
        scope=SCOPE,
        command=DeclineTransfer(
            request_id=request.id,
            actor_reference="bob",
            declined_at=NOW + timedelta(minutes=1),
            reason="in the middle of an outage call",
        ),
    )
    assert declined.status is TransferStatus.DECLINED
    db.refresh(held)
    assert (held.agent_reference, held.status) == ("ann", AssignmentStatus.ASSIGNED)


def test_an_unanswered_request_fails_back_on_its_acceptance_sla(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    held = _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue, expires_in=120)
    expired = expire_transfer_requests(
        db,
        scope=SCOPE,
        command=ExpireTransferRequests(expired_at=NOW + timedelta(seconds=121)),
    )
    assert expired == (request.id,)
    db.refresh(request)
    db.refresh(held)
    assert request.status is TransferStatus.EXPIRED
    assert request.settlement_reason == "transfer acceptance SLA elapsed"
    assert held.status is AssignmentStatus.ASSIGNED


def test_accepting_after_the_window_closed_is_refused(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue, expires_in=60)
    with pytest.raises(Conflict, match="acceptance window has closed"):
        accept_transfer(
            db,
            scope=SCOPE,
            command=AcceptTransfer(
                request_id=request.id,
                actor_reference="bob",
                accepted_at=NOW + timedelta(seconds=61),
                presence_fresh_after=FRESH_AFTER,
                eligible_agent_references=("ann", "bob"),
            ),
        )


def test_two_open_requests_for_one_conversation_are_refused(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    _warm(db, queue)
    with pytest.raises(Conflict, match="already open"):
        _warm(db, queue)


def test_a_cold_transfer_supersedes_an_open_warm_request(db: Session) -> None:
    """Otherwise the old target could still accept work whose owner changed."""
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _available(db, "carol")
    _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue)
    transfer_conversation(
        db,
        scope=SCOPE,
        command=TransferConversation(
            conversation_reference="conv-1",
            to_agent_reference="carol",
            reason="carol is free now",
            actor_reference="ann",
            transferred_at=NOW + timedelta(minutes=1),
            presence_fresh_after=FRESH_AFTER,
            eligible_agent_references=("ann", "bob", "carol"),
        ),
    )
    db.refresh(request)
    assert request.status is TransferStatus.CANCELLED
    assert request.settlement_reason == "superseded by a cold transfer"


def test_a_requester_can_withdraw_an_open_request(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue)
    cancelled = cancel_transfer(
        db,
        scope=SCOPE,
        command=CancelTransfer(
            request_id=request.id,
            actor_reference="ann",
            cancelled_at=NOW + timedelta(minutes=1),
            reason="customer answered their own question",
        ),
    )
    assert cancelled.status is TransferStatus.CANCELLED


# ── Requeue ─────────────────────────────────────────────────────────────────


def test_requeueing_returns_the_work_to_the_line_owned_by_nobody(
    db: Session,
) -> None:
    queue = _queue(db)
    _available(db, "ann")
    held = _assigned(db, queue, "conv-1", "ann")
    entry = requeue_conversation(
        db,
        scope=SCOPE,
        command=RequeueConversation(
            conversation_reference="conv-1",
            reason="wrong team",
            actor_reference="ann",
            requeued_at=NOW + timedelta(minutes=1),
        ),
    )
    db.refresh(held)
    assert held.status is AssignmentStatus.REQUEUED
    assert entry.queue_id == queue.id
    assert entry.status.value == "QUEUED"
    types = {row.event_type for row in db.scalars(select(InboxWorkflowEvent)).all()}
    assert "REQUEUED" in types


# ── Escalation ──────────────────────────────────────────────────────────────


def _escalate(db: Session, severity: str = "HIGH", *, key: str = "esc-1"):
    return escalate_conversation(
        db,
        scope=SCOPE,
        command=EscalateConversation(
            conversation_reference="conv-1",
            severity=severity,
            reason="customer threatening to cancel",
            actor_reference="ann",
            notify_reference="team-lead:support",
            requested_at=NOW,
            dedup_key=key,
            due_at=NOW + timedelta(minutes=30),
        ),
    )


def test_escalating_records_the_ask_without_moving_ownership(db: Session) -> None:
    """The distinction Sub's UI lost: "Escalate to teammate" reassigned."""
    queue = _queue(db)
    _available(db, "ann")
    held = _assigned(db, queue, "conv-1", "ann")
    requested = _escalate(db)

    assert requested.notify_reference == "team-lead:support"
    assert requested.assignment_id == held.id
    db.refresh(held)
    assert (held.agent_reference, held.status) == ("ann", AssignmentStatus.ASSIGNED)
    types = {row.event_type for row in db.scalars(select(InboxWorkflowEvent)).all()}
    assert "ESCALATION_REQUESTED" in types


def test_the_inbox_owns_the_ask_not_the_escalation(db: Session) -> None:
    """dotmac-operational-escalations owns whether an escalation should exist,
    at what level, and who answered it — for tickets, outages and inboxes
    alike. A second answer here is the drift the one-writer rule prevents, so
    the record has no status and nothing can settle it."""
    import dotmac_inbox_operations as package

    assert not hasattr(package, "acknowledge_escalation")
    assert not hasattr(package, "resolve_escalation")
    assert not hasattr(package, "EscalationStatus")
    columns = {c.name for c in InboxEscalationRequest.__table__.columns}
    assert "status" not in columns
    assert not columns & {"acknowledged_at", "resolved_at", "settled_at"}
    # Severity is passed through, never declared twice.
    assert not hasattr(package, "EscalationSeverity")


def test_an_escalation_request_is_idempotent_on_its_dedup_key(db: Session) -> None:
    """The same key the escalation owner dedupes on, so a retried sweep or a
    double-clicked button cannot become two escalations on either side."""
    queue = _queue(db)
    _available(db, "ann")
    _assigned(db, queue, "conv-1", "ann")
    first = _escalate(db, key="sla-sweep:conv-1:1")
    second = _escalate(db, key="sla-sweep:conv-1:1")
    assert first.request_id == second.request_id
    assert len(db.scalars(select(InboxEscalationRequest)).all()) == 1

    with pytest.raises(Conflict, match="dedup key was reused"):
        escalate_conversation(
            db,
            scope=SCOPE,
            command=EscalateConversation(
                conversation_reference="conv-1",
                severity="CRITICAL",
                reason="worse now",
                actor_reference="ann",
                notify_reference="team-lead:support",
                requested_at=NOW,
                dedup_key="sla-sweep:conv-1:1",
            ),
        )


def test_an_unassigned_conversation_can_still_be_escalated(db: Session) -> None:
    """A conversation nobody has picked up is exactly the one worth escalating,
    so the assignment link is optional rather than required."""
    _queue(db)
    requested = _escalate(db, key="esc-unassigned")
    assert requested.assignment_id is None


# ── Actor-subject binding ───────────────────────────────────────────────────


def test_an_agent_cannot_set_someone_elses_presence(db: Session) -> None:
    """`presence.self` is the one code an ordinary agent holds. If these
    commands took any agent reference it would silently also be
    `presence.manage` — and arrive with no reason attached."""
    _available(db, "bob")
    with pytest.raises(Conflict, match="only act on the acting agent"):
        set_agent_presence(
            db,
            scope=SCOPE,
            command=SetAgentPresence("bob", PresenceState.AWAY, 0, NOW, "ann"),
        )
    with pytest.raises(Conflict, match="only act on the acting agent"):
        record_presence_heartbeat(
            db,
            scope=SCOPE,
            command=RecordPresenceHeartbeat(
                agent_reference="bob", observed_at=NOW, actor_reference="ann"
            ),
        )


def test_an_agent_cannot_claim_work_onto_a_colleague(db: Session) -> None:
    queue = _queue(db)
    _available(db, "bob")
    admit_to_queue(
        db,
        scope=SCOPE,
        command=AdmitToQueue(queue_id=queue.id, conversation_reference="conv-1"),
    )
    with pytest.raises(Conflict, match="only be made for the acting agent"):
        claim_conversation(
            db,
            scope=SCOPE,
            command=ClaimConversation(
                conversation_reference="conv-1",
                agent_reference="bob",
                claimed_at=NOW,
                presence_fresh_after=FRESH_AFTER,
                eligible_queue_ids=(queue.id,),
                actor_reference="ann",
            ),
        )


def test_a_peer_cannot_move_a_colleagues_conversation(db: Session) -> None:
    """The transfer permission proves the actor may transfer SOMETHING. Whose
    conversation is a different question, and only an override answers it."""
    queue = _queue(db)
    for agent in ("ann", "bob", "carol"):
        _available(db, agent)
    _assigned(db, queue, "conv-1", "ann")
    command = TransferConversation(
        conversation_reference="conv-1",
        to_agent_reference="carol",
        reason="taking this off ann",
        actor_reference="bob",
        transferred_at=NOW,
        presence_fresh_after=FRESH_AFTER,
        eligible_agent_references=("ann", "bob", "carol"),
    )
    with pytest.raises(Conflict, match="only the current holder may transfer"):
        transfer_conversation(db, scope=SCOPE, command=command)

    moved = transfer_conversation(
        db,
        scope=SCOPE,
        command=dataclasses.replace(
            command,
            actor_reference="sam",
            supervisor_override=True,
            override_reason="ann is out sick",
        ),
    )
    assert moved.to_agent_reference == "carol"


def test_a_peer_cannot_requeue_a_colleagues_conversation(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    with pytest.raises(Conflict, match="only the current holder may requeue"):
        requeue_conversation(
            db,
            scope=SCOPE,
            command=RequeueConversation(
                conversation_reference="conv-1",
                reason="not mine",
                actor_reference="bob",
                requeued_at=NOW,
            ),
        )


def test_a_stranger_cannot_cancel_someone_elses_transfer_request(
    db: Session,
) -> None:
    queue = _queue(db)
    for agent in ("ann", "bob", "carol"):
        _available(db, agent)
    _assigned(db, queue, "conv-1", "ann")
    request = _warm(db, queue)
    with pytest.raises(Conflict, match="requester or current holder"):
        cancel_transfer(
            db,
            scope=SCOPE,
            command=CancelTransfer(
                request_id=request.id,
                actor_reference="carol",
                cancelled_at=NOW + timedelta(minutes=1),
                reason="meddling",
            ),
        )


# ── The offline sweep cannot act on stale ownership ─────────────────────────


def test_a_transfer_during_the_grace_period_defeats_the_offline_requeue(
    db: Session,
) -> None:
    """The race the sweep must not lose: A goes offline holding a conversation,
    a supervisor legitimately transfers it to B, then the grace period expires.
    Requeueing now would take live work off B, who is sitting there working it."""
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    end_agent_session(
        db,
        scope=SCOPE,
        command=EndAgentSession(
            agent_reference="ann",
            occurred_at=NOW,
            disposition=OfflineDisposition.REQUEUE,
            grace_seconds=60,
            reason="agent vanished",
        ),
    )
    transfer_conversation(
        db,
        scope=SCOPE,
        command=TransferConversation(
            conversation_reference="conv-1",
            to_agent_reference="bob",
            reason="covering for ann",
            actor_reference="sam",
            transferred_at=NOW + timedelta(seconds=10),
            presence_fresh_after=FRESH_AFTER,
            supervisor_override=True,
            override_reason="ann went offline mid-conversation",
        ),
    )

    settled_at = NOW + timedelta(seconds=120)
    result = settle_offline_dispositions(
        db,
        scope=SCOPE,
        command=SettleOfflineDispositions(
            settled_at=settled_at,
            presence_fresh_after=settled_at - timedelta(minutes=5),
        ),
    )
    assert not result.requeued
    assert len(result.cancelled) == 1
    live = db.scalars(
        select(ConversationAssignment).where(
            ConversationAssignment.status == AssignmentStatus.ASSIGNED
        )
    ).one()
    assert live.agent_reference == "bob"


def test_signing_out_works_for_an_agent_who_never_chose_a_state(
    db: Session,
) -> None:
    """Signing in deliberately creates no presence, so refusing here would mean
    a sign-out that cannot be recorded and held work that gets no disposition."""
    queue = _queue(db)
    _available(db, "ann")
    _assigned(db, queue, "conv-1", "ann")
    # bob never chose anything, but a supervisor put work on him.
    ended = end_agent_session(
        db,
        scope=SCOPE,
        command=EndAgentSession(
            agent_reference="bob",
            occurred_at=NOW,
            disposition=OfflineDisposition.RETAIN,
            grace_seconds=0,
            reason="session ended",
        ),
    )
    assert ended.state is PresenceState.OFFLINE


# ── The promised alerts are actually sent ───────────────────────────────────


def _events(db: Session) -> dict[str, dict]:
    rows = db.scalars(select(OutboxEvent)).all()
    return {row.event_type: row.payload for row in rows}


def test_a_warm_request_notifies_its_target(db: Session) -> None:
    """A `notify_reference` sitting in a column is a note about who someone
    ought to tell. The request must actually reach the target."""
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    _warm(db, queue)
    events = _events(db)
    assert "inbox_operations.transfer_requested.v1" in events
    payload = events["inbox_operations.transfer_requested.v1"]
    assert payload["notify_reference"] == "agent:bob"
    assert payload["to_agent_reference"] == "bob"


def test_an_escalation_alerts_its_lead(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _assigned(db, queue, "conv-1", "ann")
    _escalate(db)
    payload = _events(db)["inbox_operations.escalation_requested.v1"]
    assert payload["notify_reference"] == "team-lead:support"
    assert payload["severity"] == "HIGH"


def test_naming_nobody_asks_for_no_alert(db: Session) -> None:
    """A caller that named no target asked for no notification — an event with
    nowhere to go would be an undeliverable row the relay retries forever."""
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    transfer_conversation(
        db,
        scope=SCOPE,
        command=TransferConversation(
            conversation_reference="conv-1",
            to_agent_reference="bob",
            reason="handover",
            actor_reference="ann",
            transferred_at=NOW,
            presence_fresh_after=FRESH_AFTER,
            eligible_agent_references=("ann", "bob"),
        ),
    )
    assert not db.scalars(select(OutboxEvent)).all()


# ── The trail says who ──────────────────────────────────────────────────────


def test_every_ownership_move_records_its_actor(db: Session) -> None:
    queue = _queue(db)
    _available(db, "ann")
    _available(db, "bob")
    _assigned(db, queue, "conv-1", "ann")
    transfer_conversation(
        db,
        scope=SCOPE,
        command=TransferConversation(
            conversation_reference="conv-1",
            to_agent_reference="bob",
            reason="bob has the context",
            actor_reference="ann",
            transferred_at=NOW + timedelta(minutes=1),
            presence_fresh_after=FRESH_AFTER,
            eligible_agent_references=("ann", "bob"),
        ),
    )
    requeue_conversation(
        db,
        scope=SCOPE,
        command=RequeueConversation(
            conversation_reference="conv-1",
            reason="wrong team after all",
            actor_reference="bob",
            requeued_at=NOW + timedelta(minutes=2),
        ),
    )
    actors = {
        row.event_type: row.actor_reference
        for row in db.scalars(select(InboxWorkflowEvent)).all()
    }
    assert actors["TRANSFERRED_OUT"] == "ann"
    assert actors["TRANSFERRED_IN"] == "ann"
    assert actors["REQUEUED"] == "bob"
