"""Flush-only command state; provider execution remains in Integrator."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_network_control.contracts import (
    ApproveCommand,
    CommandLookup,
    CommandSnapshot,
    CommandState,
    DispatchEnvelope,
    DispatchQuery,
    ExecutionEvidence,
    ExecutionEvidenceQuery,
    ExecutionOutcome,
    MarkDispatched,
    ReconcileCommand,
    ReconciliationReport,
    RecordExecutionObservation,
    RecoverCommand,
    RecoveryResult,
    RejectCommand,
    RequestCommand,
)
from dotmac_network_control.models import (
    Command,
    CommandEvent,
    Dispatch,
    ExecutionEvidenceRow,
    ReconciliationRun,
)


class ControlError(ValueError):
    pass


class ControlNotFound(ControlError):
    pass


class ControlConflict(ControlError):
    pass


def _clean(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise ControlError(f"{label} must not be blank")
    return result


def _command(
    db: Session, tenant_id: UUID, command_id: UUID, *, lock: bool = False
) -> Command:
    statement = select(Command).where(
        Command.tenant_id == tenant_id, Command.id == command_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise ControlNotFound("command not found")
    return row


def _snapshot(row: Command) -> CommandSnapshot:
    return CommandSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        operation_code=row.operation_code,
        target_ref=row.target_ref,
        capability_code=row.capability_code,
        parameters=tuple((pair[0], pair[1]) for pair in row.parameters),
        request_fingerprint=row.request_fingerprint,
        correlation_ref=row.correlation_ref,
        requested_by_ref=row.requested_by_ref,
        state=CommandState(row.state),
        requested_at=row.requested_at,
        approved_at=row.approved_at,
        terminal_at=row.terminal_at,
    )


def _event(
    db: Session,
    row: Command,
    kind: str,
    evidence_ref: str,
    payload: dict[str, str],
    occurred_at: datetime,
) -> None:
    db.add(
        CommandEvent(
            tenant_id=row.tenant_id,
            command_id=row.id,
            event_type=kind,
            evidence_ref=evidence_ref,
            payload=payload,
            occurred_at=occurred_at,
        )
    )


def request_command(
    db: Session, *, tenant_id: UUID, command: RequestCommand
) -> CommandSnapshot:
    correlation = _clean(command.correlation_ref, "correlation reference")
    existing = db.scalar(
        select(Command).where(
            Command.tenant_id == tenant_id, Command.correlation_ref == correlation
        )
    )
    if existing is not None:
        if existing.request_fingerprint != command.request_fingerprint:
            raise ControlConflict(
                "correlation reference reused with another fingerprint"
            )
        return _snapshot(existing)
    now = datetime.now(UTC)
    state = (
        CommandState.REQUESTED if command.requires_approval else CommandState.APPROVED
    )
    row = Command(
        tenant_id=tenant_id,
        operation_code=_clean(command.operation_code, "operation code"),
        target_ref=_clean(command.target_ref, "target reference"),
        capability_code=_clean(command.capability_code, "capability code"),
        parameters=[list(pair) for pair in command.parameters],
        request_fingerprint=_clean(command.request_fingerprint, "request fingerprint"),
        correlation_ref=correlation,
        requested_by_ref=_clean(command.requested_by_ref, "requester reference"),
        state=state.value,
        requested_at=now,
        approved_at=now if state is CommandState.APPROVED else None,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db, row, "command_requested", correlation, {"state": state.value}, now
            )
            db.flush()
    except IntegrityError as exc:
        raise ControlConflict("command correlation already exists") from exc
    return _snapshot(row)


def approve_command(
    db: Session, *, tenant_id: UUID, command: ApproveCommand
) -> CommandSnapshot:
    row = _command(db, tenant_id, command.command_id, lock=True)
    if (
        CommandState(row.state) is not command.expected
        or command.expected is not CommandState.REQUESTED
    ):
        raise ControlConflict("command approval state changed")
    row.state = CommandState.APPROVED.value
    row.approved_at = command.approved_at
    _event(
        db,
        row,
        "command_approved",
        _clean(command.approval_ref, "approval reference"),
        {"approved_by_ref": command.approved_by_ref},
        command.approved_at,
    )
    db.flush()
    return _snapshot(row)


def reject_command(
    db: Session, *, tenant_id: UUID, command: RejectCommand
) -> CommandSnapshot:
    row = _command(db, tenant_id, command.command_id, lock=True)
    if (
        CommandState(row.state) is not command.expected
        or command.expected is not CommandState.REQUESTED
    ):
        raise ControlConflict("command rejection state changed")
    row.state = CommandState.REJECTED.value
    row.terminal_at = command.rejected_at
    _event(
        db,
        row,
        "command_rejected",
        command.rejected_by_ref,
        {"reason": _clean(command.reason, "rejection reason")},
        command.rejected_at,
    )
    db.flush()
    return _snapshot(row)


def mark_dispatched(
    db: Session, *, tenant_id: UUID, command: MarkDispatched
) -> DispatchEnvelope:
    row = _command(db, tenant_id, command.command_id, lock=True)
    if (
        CommandState(row.state) is not command.expected
        or command.expected is not CommandState.APPROVED
    ):
        raise ControlConflict("command is not dispatch-ready")
    dispatch = Dispatch(
        tenant_id=tenant_id,
        command_id=row.id,
        dispatch_ref=_clean(command.dispatch_ref, "dispatch reference"),
        plugin_capability=_clean(command.plugin_capability, "plugin capability"),
        dispatched_at=command.dispatched_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(dispatch)
            row.state = CommandState.DISPATCHED.value
            db.flush()
            _event(
                db,
                row,
                "command_dispatched",
                dispatch.dispatch_ref,
                {"plugin_capability": dispatch.plugin_capability},
                command.dispatched_at,
            )
            db.flush()
    except IntegrityError as exc:
        raise ControlConflict("dispatch reference already exists") from exc
    return DispatchEnvelope(
        command_id=row.id,
        tenant_id=tenant_id,
        dispatch_ref=dispatch.dispatch_ref,
        capability_code=row.capability_code,
        target_ref=row.target_ref,
        parameters=tuple((pair[0], pair[1]) for pair in row.parameters),
        request_fingerprint=row.request_fingerprint,
    )


def record_execution_observation(
    db: Session, *, tenant_id: UUID, command: RecordExecutionObservation
) -> ExecutionEvidence:
    row = _command(db, tenant_id, command.command_id, lock=True)
    dispatch = db.scalar(
        select(Dispatch).where(
            Dispatch.tenant_id == tenant_id,
            Dispatch.command_id == row.id,
            Dispatch.dispatch_ref == command.dispatch_ref,
        )
    )
    if dispatch is None:
        raise ControlNotFound("dispatch not found")
    existing = db.scalar(
        select(ExecutionEvidenceRow).where(
            ExecutionEvidenceRow.tenant_id == tenant_id,
            ExecutionEvidenceRow.dispatch_ref == command.dispatch_ref,
            ExecutionEvidenceRow.result_fingerprint == command.result_fingerprint,
        )
    )
    if existing is None:
        if CommandState(row.state) is not CommandState.DISPATCHED:
            raise ControlConflict(
                "new execution evidence requires a dispatched command"
            )
        evidence = ExecutionEvidenceRow(
            tenant_id=tenant_id,
            command_id=row.id,
            dispatch_ref=dispatch.dispatch_ref,
            outcome=command.outcome.value,
            observed_at=command.observed_at,
            evidence_ref=_clean(command.evidence_ref, "evidence reference"),
            result_fingerprint=_clean(command.result_fingerprint, "result fingerprint"),
            error_code=command.error_code,
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(evidence)
                if command.outcome is ExecutionOutcome.SUCCEEDED:
                    row.state = CommandState.SUCCEEDED.value
                    row.terminal_at = command.observed_at
                elif command.outcome is ExecutionOutcome.FAILED:
                    row.state = CommandState.FAILED.value
                    row.terminal_at = command.observed_at
                _event(
                    db,
                    row,
                    "execution_observed",
                    evidence.evidence_ref,
                    {"outcome": command.outcome.value},
                    command.observed_at,
                )
                db.flush()
        except IntegrityError as exc:
            raise ControlConflict("execution evidence already exists") from exc
    else:
        evidence = existing
        if evidence.outcome != command.outcome.value:
            raise ControlConflict("result fingerprint reused with another outcome")
    return ExecutionEvidence(
        id=evidence.id,
        tenant_id=evidence.tenant_id,
        command_id=evidence.command_id,
        dispatch_ref=evidence.dispatch_ref,
        outcome=ExecutionOutcome(evidence.outcome),
        observed_at=evidence.observed_at,
        evidence_ref=evidence.evidence_ref,
        result_fingerprint=evidence.result_fingerprint,
        error_code=evidence.error_code,
    )


def reconcile_command(
    db: Session, *, tenant_id: UUID, command: ReconcileCommand
) -> ReconciliationReport:
    row = _command(db, tenant_id, command.command_id)
    expected = set(
        db.scalars(
            select(Dispatch.dispatch_ref).where(
                Dispatch.tenant_id == tenant_id, Dispatch.command_id == row.id
            )
        )
    )
    observed = set(command.observed_dispatch_refs)
    missing = tuple(sorted(expected - observed))
    unexpected = tuple(sorted(observed - expected))
    drifted = bool(missing or unexpected)
    run = ReconciliationRun(
        tenant_id=tenant_id,
        command_id=row.id,
        missing_dispatch_refs=list(missing),
        unexpected_dispatch_refs=list(unexpected),
        changed=drifted,
        reconciled_at=command.as_of,
    )
    db.add(run)
    db.flush()
    return ReconciliationReport(
        command=_snapshot(row),
        missing_dispatch_refs=missing,
        unexpected_dispatch_refs=unexpected,
        changed=drifted,
        reconciled_at=command.as_of,
    )


def recover_command(
    db: Session, *, tenant_id: UUID, command: RecoverCommand
) -> RecoveryResult:
    row = _command(db, tenant_id, command.command_id, lock=True)
    current = CommandState(row.state)
    if current is not command.expected or current is not CommandState.FAILED:
        raise ControlConflict("only expected failed commands can recover")
    row.state = CommandState.APPROVED.value
    row.terminal_at = None
    _event(
        db,
        row,
        "command_recovered",
        _clean(command.evidence_ref, "evidence reference"),
        {"requested_by_ref": command.requested_by_ref},
        command.requested_at,
    )
    db.flush()
    return RecoveryResult(command=_snapshot(row), dispatch=None, recovered=True)


def lookup_commands(
    db: Session, *, tenant_id: UUID, query: CommandLookup
) -> tuple[CommandSnapshot, ...]:
    statement = select(Command).where(Command.tenant_id == tenant_id)
    if query.command_id is not None:
        statement = statement.where(Command.id == query.command_id)
    if query.correlation_ref is not None:
        statement = statement.where(Command.correlation_ref == query.correlation_ref)
    return tuple(_snapshot(row) for row in db.scalars(statement))


def query_dispatches(
    db: Session, *, tenant_id: UUID, query: DispatchQuery
) -> tuple[DispatchEnvelope, ...]:
    row = _command(db, tenant_id, query.command_id)
    if not query.include_terminal and CommandState(row.state) in {
        CommandState.SUCCEEDED,
        CommandState.FAILED,
        CommandState.REJECTED,
        CommandState.CANCELLED,
    }:
        return ()
    return tuple(
        DispatchEnvelope(
            command_id=row.id,
            tenant_id=tenant_id,
            dispatch_ref=dispatch.dispatch_ref,
            capability_code=row.capability_code,
            target_ref=row.target_ref,
            parameters=tuple((pair[0], pair[1]) for pair in row.parameters),
            request_fingerprint=row.request_fingerprint,
        )
        for dispatch in db.scalars(
            select(Dispatch).where(
                Dispatch.tenant_id == tenant_id, Dispatch.command_id == row.id
            )
        )
    )


def query_execution_evidence(
    db: Session, *, tenant_id: UUID, query: ExecutionEvidenceQuery
) -> tuple[ExecutionEvidence, ...]:
    statement = select(ExecutionEvidenceRow).where(
        ExecutionEvidenceRow.tenant_id == tenant_id,
        ExecutionEvidenceRow.command_id == query.command_id,
    )
    if query.since is not None:
        statement = statement.where(ExecutionEvidenceRow.observed_at >= query.since)
    return tuple(
        ExecutionEvidence(
            id=row.id,
            tenant_id=row.tenant_id,
            command_id=row.command_id,
            dispatch_ref=row.dispatch_ref,
            outcome=ExecutionOutcome(row.outcome),
            observed_at=row.observed_at,
            evidence_ref=row.evidence_ref,
            result_fingerprint=row.result_fingerprint,
            error_code=row.error_code,
        )
        for row in db.scalars(statement)
    )


__all__ = [
    "ControlConflict",
    "ControlError",
    "ControlNotFound",
    "approve_command",
    "lookup_commands",
    "mark_dispatched",
    "query_dispatches",
    "query_execution_evidence",
    "reconcile_command",
    "record_execution_observation",
    "recover_command",
    "reject_command",
    "request_command",
]
