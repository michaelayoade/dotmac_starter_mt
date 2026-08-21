"""Execution services against real rows; tenancy itself is Postgres-only."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_work_orders import (
    AddEvidence,
    AddNote,
    AssignWorkOrder,
    CommandConflict,
    CompletionBlocked,
    CreateWorkOrder,
    Event,
    ExecutionEvent,
    OpenWorkLogConflict,
    RecordWorkLog,
    Status,
    UnassignWorkOrder,
    UpdateWorkOrder,
    add_evidence,
    add_note,
    apply_execution_event,
    assign_work_order,
    create_work_order,
    record_worklog,
    unassign_work_order,
    update_work_order,
)
from dotmac_work_orders.models import (
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderEvent,
    WorkOrderEvidence,
    WorkOrderNote,
    WorkOrderWorkLog,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
ACTOR = uuid.uuid4()
ASSIGNEE = uuid.uuid4()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_workorders": None}},
    )
    for model in (
        IdempotencyRecord,
        WorkOrder,
        WorkOrderAssignment,
        WorkOrderEvent,
        WorkOrderWorkLog,
        WorkOrderNote,
        WorkOrderEvidence,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _create(db: Session, *, key: str = "create-1") -> WorkOrder:
    return create_work_order(
        db,
        tenant_id=TENANT,
        command=CreateWorkOrder(
            public_id="WO-1001",
            title="Install fibre service",
            status=Status.SCHEDULED,
            priority="normal",
            work_type="installation",
            idempotency_key=key,
            minimum_photo_count=1,
            customer_signoff_required=True,
        ),
    )


def _assign(db: Session, row: WorkOrder) -> None:
    assign_work_order(
        db,
        tenant_id=TENANT,
        work_order_id=row.id,
        command=AssignWorkOrder(
            assignee_id=ASSIGNEE,
            assignee_kind="technician",
            assigned_by_id=ACTOR,
            client_assignment_id=uuid.uuid4(),
        ),
    )


def _event(
    event: Event, *, client_id: uuid.UUID | None = None, **kwargs: object
) -> ExecutionEvent:
    return ExecutionEvent(
        event=event,
        client_event_id=client_id or uuid.uuid4(),
        actor_id=ACTOR,
        occurred_at=datetime.now(UTC),
        **kwargs,  # type: ignore[arg-type]
    )


def test_create_replays_the_same_command_and_conflicts_on_changed_content(
    db: Session,
) -> None:
    first = _create(db)
    replay = _create(db)
    assert replay.id == first.id
    assert db.scalars(select(WorkOrder)).all() == [first]

    with pytest.raises(CommandConflict):
        create_work_order(
            db,
            tenant_id=TENANT,
            command=CreateWorkOrder(
                public_id="WO-CHANGED",
                title="Different command",
                status=Status.SCHEDULED,
                priority="normal",
                idempotency_key="create-1",
            ),
        )


def test_assignment_is_durable_history_and_one_current_projection(db: Session) -> None:
    row = _create(db)
    _assign(db, row)
    assert row.status == Status.DISPATCHED.value
    assert row.current_assignee_id == ASSIGNEE
    assignment = db.scalars(select(WorkOrderAssignment)).one()
    assert assignment.assignee_kind == "technician"
    assert assignment.active is True

    next_assignee = uuid.uuid4()
    assign_work_order(
        db,
        tenant_id=TENANT,
        work_order_id=row.id,
        command=AssignWorkOrder(
            assignee_id=next_assignee,
            assignee_kind="technician",
            assigned_by_id=ACTOR,
            client_assignment_id=uuid.uuid4(),
        ),
    )
    assignments = db.scalars(
        select(WorkOrderAssignment).order_by(WorkOrderAssignment.assigned_at)
    ).all()
    assert [entry.active for entry in assignments] == [False, True]
    assert row.current_assignee_id == next_assignee


def test_unassignment_ends_history_and_clears_the_current_projection(
    db: Session,
) -> None:
    row = _create(db)
    _assign(db, row)
    command = UnassignWorkOrder(
        unassigned_by_id=ACTOR,
        client_unassignment_id=uuid.uuid4(),
        reason="Technician unavailable",
    )
    first = unassign_work_order(
        db, tenant_id=TENANT, work_order_id=row.id, command=command
    )
    replay = unassign_work_order(
        db, tenant_id=TENANT, work_order_id=row.id, command=command
    )
    assert replay.id == first.id
    assert first.active is False
    assert first.unassigned_by_id == ACTOR
    assert first.unassignment_reason == "Technician unavailable"
    assert row.current_assignee_id is None
    assert row.status == Status.SCHEDULED.value


def test_header_update_is_exactly_replayed_and_cannot_write_lifecycle_status(
    db: Session,
) -> None:
    row = _create(db)
    command_id = uuid.uuid4()
    command = UpdateWorkOrder(
        client_command_id=command_id,
        title="Install and commission service",
        priority="urgent",
        required_skills=["fibre", "splicing"],
    )
    first = update_work_order(
        db, tenant_id=TENANT, work_order_id=row.id, command=command
    )
    replay = update_work_order(
        db, tenant_id=TENANT, work_order_id=row.id, command=command
    )
    assert replay.id == first.id
    assert row.title == "Install and commission service"
    assert row.priority == "urgent"
    assert row.required_skills == ["fibre", "splicing"]

    with pytest.raises(CommandConflict):
        update_work_order(
            db,
            tenant_id=TENANT,
            work_order_id=row.id,
            command=command.model_copy(update={"priority": "normal"}),
        )
    assert "status" not in UpdateWorkOrder.model_fields


def test_event_replay_is_exact_and_changed_fingerprint_conflicts(db: Session) -> None:
    row = _create(db)
    _assign(db, row)
    client_id = uuid.uuid4()
    command = _event(Event.START, client_id=client_id)
    first = apply_execution_event(
        db, tenant_id=TENANT, work_order_id=row.id, command=command
    )
    replay = apply_execution_event(
        db, tenant_id=TENANT, work_order_id=row.id, command=command
    )
    assert first.event.id == replay.event.id
    assert replay.replayed
    assert row.status == Status.IN_PROGRESS.value
    assert len(db.scalars(select(WorkOrderEvent)).all()) == 1

    with pytest.raises(CommandConflict):
        apply_execution_event(
            db,
            tenant_id=TENANT,
            work_order_id=row.id,
            command=_event(Event.PAUSE, client_id=client_id),
        )


def test_completion_is_blocked_until_evidence_contract_is_satisfied(
    db: Session,
) -> None:
    row = _create(db)
    _assign(db, row)
    apply_execution_event(
        db, tenant_id=TENANT, work_order_id=row.id, command=_event(Event.START)
    )

    with pytest.raises(CompletionBlocked, match="photo"):
        apply_execution_event(
            db, tenant_id=TENANT, work_order_id=row.id, command=_event(Event.COMPLETE)
        )

    add_evidence(
        db,
        tenant_id=TENANT,
        work_order_id=row.id,
        command=AddEvidence(
            kind="photo",
            artifact_reference="files:photo-1",
            recorded_by_id=ACTOR,
            client_evidence_id=uuid.uuid4(),
        ),
    )
    with pytest.raises(CompletionBlocked, match="signature"):
        apply_execution_event(
            db, tenant_id=TENANT, work_order_id=row.id, command=_event(Event.COMPLETE)
        )

    outcome = apply_execution_event(
        db,
        tenant_id=TENANT,
        work_order_id=row.id,
        command=_event(
            Event.COMPLETE,
            payload={"signature_unavailable_reason": "customer not on site"},
        ),
    )
    assert outcome.work_order.status == Status.COMPLETED.value
    assert outcome.work_order.completed_at is not None


def test_one_actor_cannot_have_two_open_execution_timers(db: Session) -> None:
    first = _create(db, key="first")
    _assign(db, first)
    apply_execution_event(
        db, tenant_id=TENANT, work_order_id=first.id, command=_event(Event.START)
    )

    second = create_work_order(
        db,
        tenant_id=TENANT,
        command=CreateWorkOrder(
            public_id="WO-1002",
            title="Second visit",
            status=Status.SCHEDULED,
            priority="normal",
            idempotency_key="second",
        ),
    )
    _assign(db, second)
    with pytest.raises(OpenWorkLogConflict):
        apply_execution_event(
            db, tenant_id=TENANT, work_order_id=second.id, command=_event(Event.START)
        )


def test_pause_closes_the_open_worklog_with_nonnegative_minutes(db: Session) -> None:
    row = _create(db)
    _assign(db, row)
    started = datetime.now(UTC) - timedelta(minutes=31)
    apply_execution_event(
        db,
        tenant_id=TENANT,
        work_order_id=row.id,
        command=ExecutionEvent(
            event=Event.START,
            client_event_id=uuid.uuid4(),
            actor_id=ACTOR,
            occurred_at=started,
        ),
    )
    apply_execution_event(
        db, tenant_id=TENANT, work_order_id=row.id, command=_event(Event.PAUSE)
    )
    log = db.scalars(select(WorkOrderWorkLog)).one()
    assert log.ended_at is not None
    assert log.minutes >= 30


def test_note_and_manual_worklog_replays_are_fingerprint_exact(db: Session) -> None:
    row = _create(db)
    note_id = uuid.uuid4()
    note = AddNote(
        body="Customer confirmed access",
        author_id=ACTOR,
        client_note_id=note_id,
    )
    assert (
        add_note(db, tenant_id=TENANT, work_order_id=row.id, command=note).id
        == add_note(db, tenant_id=TENANT, work_order_id=row.id, command=note).id
    )
    with pytest.raises(CommandConflict):
        add_note(
            db,
            tenant_id=TENANT,
            work_order_id=row.id,
            command=note.model_copy(update={"internal": False}),
        )

    started = datetime.now(UTC) - timedelta(hours=1)
    log_id = uuid.uuid4()
    log = RecordWorkLog(
        actor_id=ACTOR,
        started_at=started,
        ended_at=started + timedelta(minutes=45),
        client_worklog_id=log_id,
    )
    assert (
        record_worklog(db, tenant_id=TENANT, work_order_id=row.id, command=log).id
        == record_worklog(db, tenant_id=TENANT, work_order_id=row.id, command=log).id
    )
    with pytest.raises(CommandConflict):
        record_worklog(
            db,
            tenant_id=TENANT,
            work_order_id=row.id,
            command=log.model_copy(
                update={"ended_at": started + timedelta(minutes=50)}
            ),
        )
