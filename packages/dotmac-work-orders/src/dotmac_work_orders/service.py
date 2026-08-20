"""The sole writer for work-order execution state.

Every mutation adds and flushes inside the caller's transaction. The host owns
commit/rollback, so a product can atomically record its prerequisite verdict,
the execution transition and its outbox consequence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from dotmac_kernel.idempotency import (
    IdempotencyConflict,
    execute_once,
    fingerprint_of,
)
from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_work_orders.lifecycle import (
    TERMINAL_STATUSES,
    Event,
    Status,
    decide_transition,
)
from dotmac_work_orders.models import (
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderEvent,
    WorkOrderEvidence,
    WorkOrderNote,
    WorkOrderWorkLog,
)
from dotmac_work_orders.schemas import (
    AddEvidence,
    AddNote,
    AssignWorkOrder,
    CreateWorkOrder,
    ExecutionEvent,
    RecordWorkLog,
    UnassignWorkOrder,
    UpdateWorkOrder,
)


class WorkOrderError(Exception):
    """Base for stable domain refusals from this owner."""


class WorkOrderNotFound(WorkOrderError):
    pass


class CommandConflict(WorkOrderError):
    pass


class CompletionBlocked(WorkOrderError):
    pass


class AssignmentRefused(WorkOrderError):
    pass


class UpdateRefused(WorkOrderError):
    pass


class OpenWorkLogConflict(WorkOrderError):
    pass


class WorkLogOverlap(WorkOrderError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    work_order: WorkOrder
    event: WorkOrderEvent
    replayed: bool = False


def _fingerprint(command: object, *, work_order_id: UUID | None = None) -> str:
    document: object
    if hasattr(command, "model_dump"):
        document = command.model_dump(mode="json")
    else:  # pragma: no cover - public commands are all Pydantic models
        document = command
    if work_order_id is not None:
        document = {"work_order_id": str(work_order_id), "command": document}
    return fingerprint_of(document)


def _execute_command(
    db: Session,
    *,
    tenant_id: UUID,
    scope: str,
    key: str,
    fingerprint: str,
    operation: Callable[[Session], Mapping[str, object]],
) -> tuple[Mapping[str, object], bool]:
    """Delegate retry ownership to the kernel's single idempotency ledger."""
    try:
        outcome = execute_once(
            db,
            tenant_id=tenant_id,
            scope=scope,
            key=key,
            fingerprint=fingerprint,
            operation=operation,
        )
    except IdempotencyConflict as exc:
        raise CommandConflict(
            "idempotency key was reused for different content"
        ) from exc
    except IntegrityError as exc:
        raise CommandConflict(f"{scope} conflicted with another writer") from exc
    return outcome.result, outcome.replayed


def _result_uuid(result: Mapping[str, object], key: str) -> UUID:
    value = result.get(key)
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise CommandConflict(f"idempotency result is missing {key}") from exc


def _one_work_order(
    tenant_id: UUID, work_order_id: UUID, *, for_update: bool = False
) -> Select[tuple[WorkOrder]]:
    statement = select(WorkOrder).where(
        WorkOrder.tenant_id == tenant_id, WorkOrder.id == work_order_id
    )
    return statement.with_for_update() if for_update else statement


def _work_order(
    db: Session, tenant_id: UUID, work_order_id: UUID, *, for_update: bool = False
) -> WorkOrder:
    row = db.execute(
        _one_work_order(tenant_id, work_order_id, for_update=for_update)
    ).scalar_one_or_none()
    if row is None:
        raise WorkOrderNotFound(f"no work order {work_order_id} for tenant {tenant_id}")
    return row


def create_work_order(
    db: Session, *, tenant_id: UUID, command: CreateWorkOrder
) -> WorkOrder:
    """Create once; replay only when the entire command is identical."""
    fingerprint = _fingerprint(command)

    def operation(session: Session) -> Mapping[str, object]:
        row = WorkOrder(
            tenant_id=tenant_id,
            public_id=command.public_id,
            title=command.title,
            description=command.description,
            status=command.status.value,
            priority=command.priority,
            work_type=command.work_type,
            scheduled_start=command.scheduled_start,
            scheduled_end=command.scheduled_end,
            estimated_duration_minutes=command.estimated_duration_minutes,
            address=command.address,
            access_notes=command.access_notes,
            required_skills=command.required_skills,
            tags=command.tags,
            minimum_photo_count=command.minimum_photo_count,
            customer_signoff_required=command.customer_signoff_required,
            signature_unavailable_reason_allowed=(
                command.signature_unavailable_reason_allowed
            ),
            required_evidence_kinds=command.required_evidence_kinds,
        )
        session.add(row)
        session.flush()
        return {"work_order_id": str(row.id)}

    result, _ = _execute_command(
        db,
        tenant_id=tenant_id,
        scope="work_orders.create",
        key=command.idempotency_key,
        fingerprint=fingerprint,
        operation=operation,
    )
    return _work_order(db, tenant_id, _result_uuid(result, "work_order_id"))


def update_work_order(
    db: Session,
    *,
    tenant_id: UUID,
    work_order_id: UUID,
    command: UpdateWorkOrder,
) -> WorkOrder:
    """Update product-neutral header fields without bypassing lifecycle owners."""
    fingerprint = _fingerprint(command, work_order_id=work_order_id)

    def operation(session: Session) -> Mapping[str, object]:
        row = _work_order(session, tenant_id, work_order_id, for_update=True)
        changes = command.model_dump(exclude_unset=True)
        changes.pop("client_command_id", None)
        scheduled_start = changes.get("scheduled_start", row.scheduled_start)
        scheduled_end = changes.get("scheduled_end", row.scheduled_end)
        if (
            scheduled_start is not None
            and scheduled_end is not None
            and scheduled_end <= scheduled_start
        ):
            raise UpdateRefused("scheduled_end must be after scheduled_start")
        for field, value in changes.items():
            setattr(row, field, value)
        session.flush()
        return {"work_order_id": str(row.id)}

    result, _ = _execute_command(
        db,
        tenant_id=tenant_id,
        scope="work_orders.update",
        key=str(command.client_command_id),
        fingerprint=fingerprint,
        operation=operation,
    )
    return _work_order(db, tenant_id, _result_uuid(result, "work_order_id"))


def assign_work_order(
    db: Session,
    *,
    tenant_id: UUID,
    work_order_id: UUID,
    command: AssignWorkOrder,
) -> WorkOrderAssignment:
    """Append assignment history and maintain one current projection."""
    fingerprint = _fingerprint(command, work_order_id=work_order_id)

    def operation(session: Session) -> Mapping[str, object]:
        row = _work_order(session, tenant_id, work_order_id, for_update=True)
        status = Status(row.status)
        if status in TERMINAL_STATUSES:
            raise AssignmentRefused(
                f"cannot assign a work order in terminal status {status.value}"
            )

        now = datetime.now(UTC)
        current = session.execute(
            select(WorkOrderAssignment).where(
                WorkOrderAssignment.tenant_id == tenant_id,
                WorkOrderAssignment.work_order_id == work_order_id,
                WorkOrderAssignment.active.is_(True),
            )
        ).scalars()
        for prior in current:
            prior.active = False
            prior.unassigned_at = now
        row.current_assignee_id = command.assignee_id
        row.current_assignee_kind = command.assignee_kind
        if status in {Status.DRAFT, Status.SCHEDULED}:
            row.status = Status.DISPATCHED.value
        # Release the former partial-unique row before inserting its successor;
        # flush ordering must not decide this invariant.
        session.flush()
        assignment = WorkOrderAssignment(
            tenant_id=tenant_id,
            work_order_id=work_order_id,
            assignee_id=command.assignee_id,
            assignee_kind=command.assignee_kind,
            assigned_by_id=command.assigned_by_id,
            client_assignment_id=command.client_assignment_id,
            reason=command.reason,
            assigned_at=now,
        )
        session.add(assignment)
        session.flush()
        return {"assignment_id": str(assignment.id)}

    result, _ = _execute_command(
        db,
        tenant_id=tenant_id,
        scope="work_orders.assign",
        key=str(command.client_assignment_id),
        fingerprint=fingerprint,
        operation=operation,
    )
    return db.execute(
        select(WorkOrderAssignment).where(
            WorkOrderAssignment.tenant_id == tenant_id,
            WorkOrderAssignment.id == _result_uuid(result, "assignment_id"),
        )
    ).scalar_one()


def unassign_work_order(
    db: Session,
    *,
    tenant_id: UUID,
    work_order_id: UUID,
    command: UnassignWorkOrder,
) -> WorkOrderAssignment:
    """End the current assignment while retaining its durable history."""
    fingerprint = _fingerprint(command, work_order_id=work_order_id)

    def operation(session: Session) -> Mapping[str, object]:
        row = _work_order(session, tenant_id, work_order_id, for_update=True)
        assignment = session.execute(
            select(WorkOrderAssignment).where(
                WorkOrderAssignment.tenant_id == tenant_id,
                WorkOrderAssignment.work_order_id == work_order_id,
                WorkOrderAssignment.active.is_(True),
            )
        ).scalar_one_or_none()
        if assignment is None:
            raise AssignmentRefused("work order has no active assignment")
        assignment.active = False
        assignment.unassigned_at = datetime.now(UTC)
        assignment.unassigned_by_id = command.unassigned_by_id
        assignment.unassignment_reason = (command.reason or "").strip() or None
        row.current_assignee_id = None
        row.current_assignee_kind = None
        if Status(row.status) is Status.DISPATCHED:
            row.status = Status.SCHEDULED.value
        session.flush()
        return {"assignment_id": str(assignment.id)}

    result, _ = _execute_command(
        db,
        tenant_id=tenant_id,
        scope="work_orders.unassign",
        key=str(command.client_unassignment_id),
        fingerprint=fingerprint,
        operation=operation,
    )
    return db.execute(
        select(WorkOrderAssignment).where(
            WorkOrderAssignment.tenant_id == tenant_id,
            WorkOrderAssignment.id == _result_uuid(result, "assignment_id"),
        )
    ).scalar_one()


def add_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    work_order_id: UUID,
    command: AddEvidence,
) -> WorkOrderEvidence:
    """Record evidence metadata; the artifact owner keeps the bytes."""
    fingerprint = _fingerprint(command, work_order_id=work_order_id)

    def operation(session: Session) -> Mapping[str, object]:
        _work_order(session, tenant_id, work_order_id)
        row = WorkOrderEvidence(
            tenant_id=tenant_id,
            work_order_id=work_order_id,
            kind=command.kind.strip().lower(),
            artifact_reference=command.artifact_reference,
            recorded_by_id=command.recorded_by_id,
            client_evidence_id=command.client_evidence_id,
            captured_at=command.captured_at,
            latitude=command.latitude,
            longitude=command.longitude,
            metadata_=command.metadata,
        )
        session.add(row)
        session.flush()
        return {"evidence_id": str(row.id)}

    result, _ = _execute_command(
        db,
        tenant_id=tenant_id,
        scope="work_orders.evidence.add",
        key=str(command.client_evidence_id),
        fingerprint=fingerprint,
        operation=operation,
    )
    return db.execute(
        select(WorkOrderEvidence).where(
            WorkOrderEvidence.tenant_id == tenant_id,
            WorkOrderEvidence.id == _result_uuid(result, "evidence_id"),
        )
    ).scalar_one()


def add_note(
    db: Session,
    *,
    tenant_id: UUID,
    work_order_id: UUID,
    command: AddNote,
) -> WorkOrderNote:
    fingerprint = _fingerprint(command, work_order_id=work_order_id)

    def operation(session: Session) -> Mapping[str, object]:
        _work_order(session, tenant_id, work_order_id)
        row = WorkOrderNote(
            tenant_id=tenant_id,
            work_order_id=work_order_id,
            author_id=command.author_id,
            client_note_id=command.client_note_id,
            body=command.body.strip(),
            internal=command.internal,
            metadata_=command.metadata,
            created_at=datetime.now(UTC),
        )
        session.add(row)
        session.flush()
        return {"note_id": str(row.id)}

    result, _ = _execute_command(
        db,
        tenant_id=tenant_id,
        scope="work_orders.note.add",
        key=str(command.client_note_id),
        fingerprint=fingerprint,
        operation=operation,
    )
    return db.execute(
        select(WorkOrderNote).where(
            WorkOrderNote.tenant_id == tenant_id,
            WorkOrderNote.id == _result_uuid(result, "note_id"),
        )
    ).scalar_one()


def record_worklog(
    db: Session,
    *,
    tenant_id: UUID,
    work_order_id: UUID,
    command: RecordWorkLog,
) -> WorkOrderWorkLog:
    fingerprint = _fingerprint(command, work_order_id=work_order_id)

    def operation(session: Session) -> Mapping[str, object]:
        row = _work_order(session, tenant_id, work_order_id, for_update=True)
        overlap = session.execute(
            select(WorkOrderWorkLog.id).where(
                WorkOrderWorkLog.tenant_id == tenant_id,
                WorkOrderWorkLog.actor_id == command.actor_id,
                WorkOrderWorkLog.active.is_(True),
                WorkOrderWorkLog.started_at < command.ended_at,
                or_(
                    WorkOrderWorkLog.ended_at.is_(None),
                    WorkOrderWorkLog.ended_at > command.started_at,
                ),
            )
        ).first()
        if overlap is not None:
            raise WorkLogOverlap(
                "work log overlaps existing active time for this actor"
            )
        seconds = _duration_seconds(command.started_at, command.ended_at)
        log = WorkOrderWorkLog(
            tenant_id=tenant_id,
            work_order_id=work_order_id,
            actor_id=command.actor_id,
            started_at=command.started_at,
            ended_at=command.ended_at,
            minutes=seconds // 60,
            notes=command.notes,
            client_worklog_id=command.client_worklog_id,
        )
        row.total_active_seconds += seconds
        session.add(log)
        session.flush()
        return {"worklog_id": str(log.id)}

    result, _ = _execute_command(
        db,
        tenant_id=tenant_id,
        scope="work_orders.worklog.record",
        key=str(command.client_worklog_id),
        fingerprint=fingerprint,
        operation=operation,
    )
    return db.execute(
        select(WorkOrderWorkLog).where(
            WorkOrderWorkLog.tenant_id == tenant_id,
            WorkOrderWorkLog.id == _result_uuid(result, "worklog_id"),
        )
    ).scalar_one()


def apply_execution_event(
    db: Session,
    *,
    tenant_id: UUID,
    work_order_id: UUID,
    command: ExecutionEvent,
) -> ExecutionOutcome:
    """Apply one idempotent mobile event and its generic timer effects."""
    fingerprint = _fingerprint(command, work_order_id=work_order_id)

    def operation(session: Session) -> Mapping[str, object]:
        row = _work_order(session, tenant_id, work_order_id, for_update=True)
        decision = decide_transition(Status(row.status), command.event)
        _validate_unable_reason(command)
        if decision.completes_work:
            _require_completion_evidence(session, tenant_id, row, command)

        event = WorkOrderEvent(
            tenant_id=tenant_id,
            work_order_id=work_order_id,
            actor_id=command.actor_id,
            event=command.event.value,
            previous_status=decision.previous_status.value,
            new_status=decision.new_status.value,
            latitude=command.latitude,
            longitude=command.longitude,
            note=(command.note or "").strip() or None,
            payload=command.payload,
            occurred_at=_as_utc(command.occurred_at),
            received_at=datetime.now(UTC),
            client_event_id=command.client_event_id,
        )
        row.status = decision.new_status.value
        _apply_timestamps(row, command.event, event.occurred_at)
        if decision.starts_work:
            _start_timer(session, tenant_id, row, command)
        if decision.stops_work:
            _stop_timer(session, tenant_id, row, command.actor_id, event.occurred_at)
        session.add(event)
        session.flush()
        return {"event_id": str(event.id), "work_order_id": str(row.id)}

    result, replayed = _execute_command(
        db,
        tenant_id=tenant_id,
        scope="work_orders.execution_event.apply",
        key=str(command.client_event_id),
        fingerprint=fingerprint,
        operation=operation,
    )
    event = db.execute(
        select(WorkOrderEvent).where(
            WorkOrderEvent.tenant_id == tenant_id,
            WorkOrderEvent.id == _result_uuid(result, "event_id"),
        )
    ).scalar_one()
    row = _work_order(db, tenant_id, _result_uuid(result, "work_order_id"))
    return ExecutionOutcome(work_order=row, event=event, replayed=replayed)


def _validate_unable_reason(command: ExecutionEvent) -> None:
    if command.event is not Event.UNABLE_TO_COMPLETE:
        return
    allowed = {
        "customer_absent",
        "no_access",
        "site_not_ready",
        "needs_parts",
        "unsafe",
        "other",
    }
    reason = command.payload.get("reason")
    if not isinstance(reason, str) or reason.strip() not in allowed:
        raise CommandConflict(
            "unable_to_complete requires a supported reason in the event payload"
        )


def _require_completion_evidence(
    db: Session, tenant_id: UUID, row: WorkOrder, command: ExecutionEvent
) -> None:
    evidence = db.execute(
        select(WorkOrderEvidence).where(
            WorkOrderEvidence.tenant_id == tenant_id,
            WorkOrderEvidence.work_order_id == row.id,
            WorkOrderEvidence.active.is_(True),
        )
    ).scalars()
    kinds = [entry.kind for entry in evidence]
    if kinds.count("photo") < row.minimum_photo_count:
        raise CompletionBlocked(
            f"completion requires {row.minimum_photo_count} photo evidence item(s)"
        )
    missing = sorted(set(row.required_evidence_kinds) - set(kinds))
    if missing:
        raise CompletionBlocked(
            f"completion is missing required evidence kinds: {', '.join(missing)}"
        )
    if row.customer_signoff_required and "signature" not in kinds:
        fallback = command.payload.get("signature_unavailable_reason")
        if not (
            row.signature_unavailable_reason_allowed
            and isinstance(fallback, str)
            and fallback.strip()
        ):
            raise CompletionBlocked(
                "completion requires a signature or an allowed signature reason"
            )


def _apply_timestamps(row: WorkOrder, event: Event, occurred_at: datetime) -> None:
    if event is Event.START:
        row.started_at = row.started_at or occurred_at
        row.paused_at = None
        row.resumed_at = None
    elif event in {Event.PAUSE, Event.HOLD}:
        row.paused_at = occurred_at
    elif event is Event.RESUME:
        row.resumed_at = occurred_at
        row.paused_at = None
    elif event is Event.COMPLETE:
        row.completed_at = occurred_at
        row.paused_at = None
    elif event is Event.UNABLE_TO_COMPLETE:
        row.paused_at = None


def _open_log(db: Session, tenant_id: UUID, actor_id: UUID) -> WorkOrderWorkLog | None:
    return (
        db.execute(
            select(WorkOrderWorkLog)
            .where(
                WorkOrderWorkLog.tenant_id == tenant_id,
                WorkOrderWorkLog.actor_id == actor_id,
                WorkOrderWorkLog.ended_at.is_(None),
                WorkOrderWorkLog.active.is_(True),
            )
            .order_by(WorkOrderWorkLog.started_at.desc())
        )
        .scalars()
        .first()
    )


def _start_timer(
    db: Session, tenant_id: UUID, row: WorkOrder, command: ExecutionEvent
) -> None:
    existing = _open_log(db, tenant_id, command.actor_id)
    if existing is not None:
        raise OpenWorkLogConflict(
            f"actor {command.actor_id} already has open work on "
            f"{existing.work_order_id}"
        )
    db.add(
        WorkOrderWorkLog(
            tenant_id=tenant_id,
            work_order_id=row.id,
            actor_id=command.actor_id,
            started_at=_as_utc(command.occurred_at),
            minutes=0,
            notes=f"Auto-started by {command.event.value}",
            client_worklog_id=uuid5(
                NAMESPACE_URL, f"work-order:{command.client_event_id}:timer"
            ),
        )
    )


def _stop_timer(
    db: Session,
    tenant_id: UUID,
    row: WorkOrder,
    actor_id: UUID,
    occurred_at: datetime,
) -> None:
    log = _open_log(db, tenant_id, actor_id)
    if log is None:
        return
    if log.work_order_id != row.id:
        raise OpenWorkLogConflict(
            f"actor {actor_id} has open work on {log.work_order_id}, not {row.id}"
        )
    log.ended_at = occurred_at
    seconds = _duration_seconds(log.started_at, occurred_at)
    log.minutes = seconds // 60
    row.total_active_seconds += seconds


def _duration_seconds(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((_as_utc(ended_at) - _as_utc(started_at)).total_seconds()))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "AssignmentRefused",
    "CommandConflict",
    "CompletionBlocked",
    "ExecutionOutcome",
    "OpenWorkLogConflict",
    "UpdateRefused",
    "WorkLogOverlap",
    "WorkOrderError",
    "WorkOrderNotFound",
    "add_evidence",
    "add_note",
    "apply_execution_event",
    "assign_work_order",
    "create_work_order",
    "record_worklog",
    "unassign_work_order",
    "update_work_order",
]
