"""Flush-only owner for fulfillment runs, attempts, outcomes, and compensation."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_fulfillment.contracts import (
    AttemptRequest,
    CompensationCommand,
    CompensationDisposition,
    CompensationOutcome,
    CompensationRecord,
    CompensationRequest,
    FulfillmentConflict,
    FulfillmentNotFound,
    OutcomeClass,
    OutcomeMessage,
    OutcomeRecord,
    ParticipantCommand,
    ReobservationSchedule,
    RepairAction,
    RepairActor,
    RepairAttention,
    RunCreate,
    RunProgress,
    RunProgressSnapshot,
    StaleOutcome,
)
from dotmac_fulfillment.models import (
    FulfillmentAttempt,
    FulfillmentCompensationReceipt,
    FulfillmentCompensationRequest,
    FulfillmentOutcomeReceipt,
    FulfillmentRun,
    FulfillmentStep,
)
from dotmac_fulfillment.participants import ParticipantRegistry

CommandPublisher = Callable[[Session, ParticipantCommand], None]
CompensationPublisher = Callable[[Session, CompensationCommand], None]
ReobservationScheduler = Callable[[Session, ReobservationSchedule], None]
RepairAuthorizer = Callable[[Session, UUID, RepairActor, RepairAction], None]

_CREATE_SCOPE = "fulfillment.run.create"
_ATTEMPT_SCOPE = "fulfillment.attempt.request"
_OUTCOME_SCOPE = "fulfillment.outcome.record"
_COMPENSATION_SCOPE = "fulfillment.compensation.request"
_COMPENSATION_OUTCOME_SCOPE = "fulfillment.compensation.record"


def _write_repair_audit(
    db: Session,
    *,
    tenant_id: UUID,
    actor: RepairActor,
    action: RepairAction,
    run_id: UUID,
    step_id: str,
    details: dict[str, object],
) -> None:
    """Write repair evidence through the kernel's one tenant audit owner."""
    from dotmac_kernel.audit import write_audit_event

    write_audit_event(
        db,
        tenant_id=tenant_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        actor_label=actor.actor_label,
        actor_party_id=actor.actor_party_id,
        action=action.value,
        entity_type="fulfillment_run",
        entity_id=str(run_id),
        details={"step_id": step_id, **details},
    )


def _run(db: Session, tenant_id: UUID, run_id: UUID) -> FulfillmentRun:
    """Resolve the run, or refuse. This does NOT lock the aggregate.

    An earlier form took `SELECT ... FOR UPDATE` on the run to serialize two
    writers on one step. It cannot: PostgreSQL requires UPDATE privilege for a
    row lock, and `fu_0001` grants the online role only SELECT and INSERT on
    every table here — deliberately, because this ledger is append-only and
    `FulfillmentRun` has no mutable column at all. Taking that lock would have
    meant granting mutation rights on an immutable aggregate purely to obtain
    mutual exclusion, which is a worse trade than the one it was buying.

    Mutual exclusion comes from the constraints that already state the rule:
    `uq_fulfillment_attempts_step_sequence` admits one attempt per
    (tenant, run, step, sequence), and `uq_fulfillment_comp_requests_attempt`
    one compensation per original attempt. A loser blocks on the index until
    the winner commits, then fails on flush — before `publish` is reached, so
    no command escapes for an effect that was rolled back — and
    `_request_attempt`/`request_compensation` translate that `IntegrityError`
    into `FulfillmentConflict`. The database enforces the invariant; the lock
    only ever moved where the refusal happened.
    """
    row = db.scalar(
        select(FulfillmentRun).where(
            FulfillmentRun.tenant_id == tenant_id, FulfillmentRun.id == run_id
        )
    )
    if row is None:
        raise FulfillmentNotFound("fulfillment run not found")
    return row


def _step_by_code(
    db: Session, tenant_id: UUID, run_id: UUID, step_id: str
) -> FulfillmentStep:
    row = db.scalar(
        select(FulfillmentStep).where(
            FulfillmentStep.tenant_id == tenant_id,
            FulfillmentStep.run_id == run_id,
            FulfillmentStep.step_id == step_id,
        )
    )
    if row is None:
        raise FulfillmentNotFound("fulfillment step not found")
    return row


def _latest_attempt(
    db: Session, tenant_id: UUID, step_record_id: UUID
) -> FulfillmentAttempt | None:
    return db.scalar(
        select(FulfillmentAttempt)
        .where(
            FulfillmentAttempt.tenant_id == tenant_id,
            FulfillmentAttempt.step_id == step_record_id,
        )
        .order_by(FulfillmentAttempt.sequence.desc())
        .limit(1)
    )


def _attempt_receipt(
    db: Session, tenant_id: UUID, attempt_id: UUID
) -> FulfillmentOutcomeReceipt | None:
    return db.scalar(
        select(FulfillmentOutcomeReceipt).where(
            FulfillmentOutcomeReceipt.tenant_id == tenant_id,
            FulfillmentOutcomeReceipt.attempt_id == attempt_id,
        )
    )


def create_run(
    db: Session,
    *,
    tenant_id: UUID,
    request: RunCreate,
    participants: ParticipantRegistry,
) -> FulfillmentRun:
    """Create one immutable run/step definition set, replaying the original."""
    from dotmac_kernel.idempotency import execute_once, fingerprint_of

    for step in request.steps:
        participants.require(step.participant_code)
    request_fingerprint = fingerprint_of(request.as_fingerprint_payload())

    def operation(session: Session) -> dict[str, object]:
        row = FulfillmentRun(
            tenant_id=tenant_id,
            intent_ref=request.intent_ref,
            idempotency_key=request.idempotency_key,
            request_fingerprint=request_fingerprint,
            correlation_id=request.correlation_id,
        )
        session.add(row)
        session.flush()
        for sequence, definition in enumerate(request.steps, start=1):
            session.add(
                FulfillmentStep(
                    tenant_id=tenant_id,
                    run_id=row.id,
                    step_id=definition.step_id,
                    sequence=sequence,
                    participant_code=definition.participant_code,
                    command_type=definition.command_type,
                    line_ref=definition.line_ref,
                    spec=dict(definition.spec),
                    spec_fingerprint=fingerprint_of(
                        definition.as_fingerprint_payload()
                    ),
                )
            )
        session.flush()
        return {"run_id": str(row.id)}

    try:
        outcome = execute_once(
            db,
            tenant_id=tenant_id,
            scope=_CREATE_SCOPE,
            key=request.idempotency_key,
            operation=operation,
            operation_name="fulfillment.create_run",
            fingerprint=request_fingerprint,
            correlation_id=request.correlation_id,
        )
    except IntegrityError as exc:
        raise FulfillmentConflict(
            "the commercial intent or run identity already has a fulfillment run"
        ) from exc
    return _run(db, tenant_id, UUID(str(outcome.result["run_id"])))


def _participant_command(
    step: FulfillmentStep, attempt: FulfillmentAttempt
) -> ParticipantCommand:
    return ParticipantCommand(
        run_id=attempt.run_id,
        step_record_id=step.id,
        attempt_id=attempt.id,
        step_id=step.step_id,
        participant_code=step.participant_code,
        command_type=step.command_type,
        command_id=attempt.command_id,
        operation_id=attempt.operation_id,
        correlation_id=attempt.correlation_id,
        causation_id=attempt.causation_id,
        spec=dict(step.spec),
        requested_at=attempt.requested_at,
    )


def _request_attempt(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    request: AttemptRequest,
    participants: ParticipantRegistry,
    publish: CommandPublisher,
    schedule_reobservation: ReobservationScheduler,
    redrive: bool,
    repair_actor: RepairActor | None = None,
    authorize_repair: RepairAuthorizer | None = None,
) -> ParticipantCommand:
    from dotmac_kernel.idempotency import execute_once, fingerprint_of

    if redrive and (repair_actor is None or authorize_repair is None):
        raise FulfillmentConflict(
            "a redrive requires an explicit actor and assembly authorizer"
        )
    request_fingerprint = fingerprint_of(
        {
            "run_id": str(run_id),
            **request.as_fingerprint_payload(),
            "repair_actor": (
                repair_actor.as_fingerprint_payload()
                if repair_actor is not None
                else None
            ),
        }
    )

    def operation(session: Session) -> dict[str, object]:
        _run(session, tenant_id, run_id)
        if redrive:
            if repair_actor is None or authorize_repair is None:
                raise FulfillmentConflict(
                    "a redrive requires an explicit actor and assembly authorizer"
                )
            authorize_repair(
                session, tenant_id, repair_actor, RepairAction.REDRIVE_ATTEMPT
            )
        step = _step_by_code(session, tenant_id, run_id, request.step_id)
        participants.require(step.participant_code)
        latest = _latest_attempt(session, tenant_id, step.id)
        if latest is not None:
            receipt = _attempt_receipt(session, tenant_id, latest.id)
            if receipt is None:
                raise FulfillmentConflict(
                    "the latest attempt is unresolved; reconcile it before redrive"
                )
            if not redrive:
                raise FulfillmentConflict(
                    "the step already has an attempt; use the reviewed redrive command"
                )
            if receipt.classification not in {
                OutcomeClass.RETRYABLE.value,
                OutcomeClass.RECONCILIATION_REQUIRED.value,
            }:
                raise FulfillmentConflict(
                    "only retryable or reconciliation-required outcomes may be redriven"
                )
        elif redrive:
            raise FulfillmentConflict("a step with no attempt cannot be redriven")

        attempt = FulfillmentAttempt(
            tenant_id=tenant_id,
            run_id=run_id,
            step_id=step.id,
            sequence=1 if latest is None else latest.sequence + 1,
            command_id=request.command_id,
            operation_id=request.operation_id,
            idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_id,
            causation_id=request.causation_id,
            requested_at=request.requested_at,
        )
        session.add(attempt)
        session.flush()
        publish(session, _participant_command(step, attempt))
        schedule_reobservation(
            session,
            ReobservationSchedule(
                run_id=run_id,
                step_record_id=step.id,
                attempt_id=attempt.id,
                participant_code=step.participant_code,
                operation_id=attempt.operation_id,
                due_at=request.reobserve_at,
            ),
        )
        if redrive:
            if repair_actor is None:
                raise FulfillmentConflict("a redrive requires an explicit actor")
            _write_repair_audit(
                session,
                tenant_id=tenant_id,
                actor=repair_actor,
                action=RepairAction.REDRIVE_ATTEMPT,
                run_id=run_id,
                step_id=step.step_id,
                details={
                    "attempt_id": str(attempt.id),
                    "operation_id": attempt.operation_id,
                },
            )
        return {"attempt_id": str(attempt.id)}

    try:
        outcome = execute_once(
            db,
            tenant_id=tenant_id,
            scope=_ATTEMPT_SCOPE,
            key=request.idempotency_key,
            operation=operation,
            operation_name=(
                "fulfillment.request_redrive"
                if redrive
                else "fulfillment.request_attempt"
            ),
            fingerprint=request_fingerprint,
            correlation_id=request.correlation_id,
        )
    except IntegrityError as exc:
        raise FulfillmentConflict("attempt identity already exists") from exc

    attempt = db.scalar(
        select(FulfillmentAttempt).where(
            FulfillmentAttempt.tenant_id == tenant_id,
            FulfillmentAttempt.id == UUID(str(outcome.result["attempt_id"])),
        )
    )
    if attempt is None:
        raise FulfillmentNotFound("recorded fulfillment attempt not found")
    step = db.scalar(
        select(FulfillmentStep).where(
            FulfillmentStep.tenant_id == tenant_id,
            FulfillmentStep.id == attempt.step_id,
        )
    )
    if step is None:
        raise FulfillmentNotFound("recorded fulfillment step not found")
    return _participant_command(step, attempt)


def request_attempt(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    request: AttemptRequest,
    participants: ParticipantRegistry,
    publish: CommandPublisher,
    schedule_reobservation: ReobservationScheduler,
) -> ParticipantCommand:
    return _request_attempt(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        request=request,
        participants=participants,
        publish=publish,
        schedule_reobservation=schedule_reobservation,
        redrive=False,
    )


def request_redrive(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    request: AttemptRequest,
    participants: ParticipantRegistry,
    publish: CommandPublisher,
    schedule_reobservation: ReobservationScheduler,
    actor: RepairActor,
    authorize: RepairAuthorizer,
) -> ParticipantCommand:
    return _request_attempt(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        request=request,
        participants=participants,
        publish=publish,
        schedule_reobservation=schedule_reobservation,
        redrive=True,
        repair_actor=actor,
        authorize_repair=authorize,
    )


def _attempt_for_message(
    db: Session, tenant_id: UUID, message: OutcomeMessage
) -> tuple[FulfillmentAttempt, FulfillmentStep]:
    attempt = db.scalar(
        select(FulfillmentAttempt).where(
            FulfillmentAttempt.tenant_id == tenant_id,
            FulfillmentAttempt.command_id == message.command_id,
        )
    )
    if attempt is None:
        raise FulfillmentNotFound("participant outcome has no matching attempt")
    step = db.scalar(
        select(FulfillmentStep).where(
            FulfillmentStep.tenant_id == tenant_id,
            FulfillmentStep.id == attempt.step_id,
        )
    )
    if step is None:
        raise FulfillmentNotFound("participant outcome step not found")
    if step.participant_code != message.participant_code:
        raise FulfillmentConflict("participant outcome does not match the step owner")
    if attempt.operation_id != message.operation_id:
        raise FulfillmentConflict("participant outcome operation_id does not match")
    latest = _latest_attempt(db, tenant_id, step.id)
    if latest is None or latest.id != attempt.id:
        raise StaleOutcome("participant outcome addresses a superseded attempt")
    return attempt, step


def record_outcome(
    db: Session,
    *,
    tenant_id: UUID,
    message: OutcomeMessage,
    schedule_reobservation: ReobservationScheduler | None = None,
) -> OutcomeRecord:
    """Record or replay one participant outcome and schedule uncertain work."""
    from dotmac_kernel.idempotency import execute_once, fingerprint_of

    attempt, step = _attempt_for_message(db, tenant_id, message)
    if (
        message.classification
        in (OutcomeClass.RETRYABLE, OutcomeClass.RECONCILIATION_REQUIRED)
        and schedule_reobservation is None
    ):
        raise FulfillmentConflict(
            "uncertain outcomes require the assembly's durable-timer scheduler"
        )
    fingerprint_payload = {
        **message.as_fingerprint_payload(),
        "classification": message.classification.value,
        "occurred_at": message.occurred_at.isoformat(),
    }
    request_fingerprint = fingerprint_of(fingerprint_payload)

    def operation(session: Session) -> dict[str, object]:
        locked_attempt, locked_step = _attempt_for_message(session, tenant_id, message)
        receipt = FulfillmentOutcomeReceipt(
            tenant_id=tenant_id,
            run_id=locked_attempt.run_id,
            step_id=locked_step.id,
            attempt_id=locked_attempt.id,
            outcome_id=message.outcome_id,
            participant_code=message.participant_code,
            command_id=message.command_id,
            operation_id=message.operation_id,
            request_fingerprint=request_fingerprint,
            classification=message.classification.value,
            provider_status=message.provider_status,
            error_class=message.error_class,
            reason_code=message.reason_code,
            detail=dict(message.detail),
            occurred_at=message.occurred_at,
        )
        session.add(receipt)
        session.flush()
        if schedule_reobservation is not None and message.reobserve_at is not None:
            schedule_reobservation(
                session,
                ReobservationSchedule(
                    run_id=locked_attempt.run_id,
                    step_record_id=locked_step.id,
                    attempt_id=locked_attempt.id,
                    participant_code=locked_step.participant_code,
                    operation_id=locked_attempt.operation_id,
                    due_at=message.reobserve_at,
                ),
            )
        return {"receipt_id": str(receipt.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope=_OUTCOME_SCOPE,
        key=message.command_id,
        operation=operation,
        operation_name="fulfillment.record_outcome",
        fingerprint=request_fingerprint,
        correlation_id=attempt.correlation_id,
    )
    receipt_id = UUID(str(outcome.result["receipt_id"]))
    receipt = db.scalar(
        select(FulfillmentOutcomeReceipt).where(
            FulfillmentOutcomeReceipt.tenant_id == tenant_id,
            FulfillmentOutcomeReceipt.id == receipt_id,
        )
    )
    if receipt is None:
        raise FulfillmentNotFound("recorded participant outcome not found")
    return OutcomeRecord(
        receipt_id=receipt.id,
        classification=OutcomeClass(receipt.classification),
        replayed=outcome.replayed,
    )


def record_reviewed_terminal_outcome(
    db: Session,
    *,
    tenant_id: UUID,
    message: OutcomeMessage,
    actor: RepairActor,
    authorize: RepairAuthorizer,
) -> OutcomeRecord:
    """Settle one unresolved attempt terminally without rewriting evidence."""
    from dotmac_kernel.idempotency import execute_once, fingerprint_of

    if message.classification is not OutcomeClass.TERMINAL:
        raise FulfillmentConflict("a reviewed outcome must be terminal")
    attempt, step = _attempt_for_message(db, tenant_id, message)
    request_fingerprint = fingerprint_of(
        {
            **message.as_fingerprint_payload(),
            "classification": message.classification.value,
            "occurred_at": message.occurred_at.isoformat(),
            "repair_actor": actor.as_fingerprint_payload(),
        }
    )

    def operation(session: Session) -> dict[str, object]:
        locked_attempt, locked_step = _attempt_for_message(session, tenant_id, message)
        authorize(
            session,
            tenant_id,
            actor,
            RepairAction.RECORD_TERMINAL_OUTCOME,
        )
        receipt = FulfillmentOutcomeReceipt(
            tenant_id=tenant_id,
            run_id=locked_attempt.run_id,
            step_id=locked_step.id,
            attempt_id=locked_attempt.id,
            outcome_id=message.outcome_id,
            participant_code=message.participant_code,
            command_id=message.command_id,
            operation_id=message.operation_id,
            request_fingerprint=request_fingerprint,
            classification=message.classification.value,
            provider_status=message.provider_status,
            error_class=message.error_class,
            reason_code=message.reason_code,
            detail=dict(message.detail),
            occurred_at=message.occurred_at,
            reviewed_by_type=actor.actor_type,
            reviewed_by_id=actor.actor_id,
        )
        session.add(receipt)
        session.flush()
        _write_repair_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=RepairAction.RECORD_TERMINAL_OUTCOME,
            run_id=locked_attempt.run_id,
            step_id=locked_step.step_id,
            details={
                "attempt_id": str(locked_attempt.id),
                "outcome_id": message.outcome_id,
                "reason_code": message.reason_code,
            },
        )
        return {"receipt_id": str(receipt.id)}

    recorded = execute_once(
        db,
        tenant_id=tenant_id,
        scope=_OUTCOME_SCOPE,
        key=message.command_id,
        operation=operation,
        operation_name="fulfillment.record_reviewed_terminal_outcome",
        fingerprint=request_fingerprint,
        correlation_id=attempt.correlation_id,
    )
    receipt = db.scalar(
        select(FulfillmentOutcomeReceipt).where(
            FulfillmentOutcomeReceipt.tenant_id == tenant_id,
            FulfillmentOutcomeReceipt.id == UUID(str(recorded.result["receipt_id"])),
        )
    )
    if receipt is None:
        raise FulfillmentNotFound("recorded reviewed terminal outcome not found")
    return OutcomeRecord(
        receipt_id=receipt.id,
        classification=OutcomeClass(receipt.classification),
        replayed=recorded.replayed,
    )


def derive_run_progress(
    db: Session, *, tenant_id: UUID, run_id: UUID
) -> RunProgressSnapshot:
    """Derive aggregate progress exclusively from immutable attempts/receipts."""
    _run(db, tenant_id, run_id)
    steps = tuple(
        db.scalars(
            select(FulfillmentStep)
            .where(
                FulfillmentStep.tenant_id == tenant_id,
                FulfillmentStep.run_id == run_id,
            )
            .order_by(FulfillmentStep.sequence)
        )
    )
    attempts = tuple(
        db.scalars(
            select(FulfillmentAttempt)
            .where(
                FulfillmentAttempt.tenant_id == tenant_id,
                FulfillmentAttempt.run_id == run_id,
            )
            .order_by(FulfillmentAttempt.sequence)
        )
    )
    latest_by_step: dict[UUID, FulfillmentAttempt] = {}
    for attempt in attempts:
        latest_by_step[attempt.step_id] = attempt
    receipts = {
        receipt.attempt_id: receipt
        for receipt in db.scalars(
            select(FulfillmentOutcomeReceipt).where(
                FulfillmentOutcomeReceipt.tenant_id == tenant_id,
                FulfillmentOutcomeReceipt.run_id == run_id,
            )
        )
    }

    by_class: dict[OutcomeClass, list[str]] = {member: [] for member in OutcomeClass}
    pending: list[str] = []
    classifications: list[OutcomeClass | None] = []
    for step in steps:
        latest = latest_by_step.get(step.id)
        receipt = receipts.get(latest.id) if latest is not None else None
        if receipt is None:
            pending.append(step.step_id)
            classifications.append(None)
            continue
        classification = OutcomeClass(receipt.classification)
        by_class[classification].append(step.step_id)
        classifications.append(classification)

    succeeded = by_class[OutcomeClass.SUCCEEDED]
    terminal = by_class[OutcomeClass.TERMINAL]
    if len(succeeded) == len(steps):
        progress = RunProgress.SUCCEEDED
    elif succeeded:
        progress = RunProgress.PARTIAL
    elif terminal and len(terminal) == len(steps):
        progress = RunProgress.FAILED
    elif attempts or any(classifications):
        progress = RunProgress.IN_PROGRESS
    else:
        progress = RunProgress.PENDING

    settled_prefix = 0
    for prefix_classification in classifications:
        if prefix_classification not in (
            OutcomeClass.SUCCEEDED,
            OutcomeClass.TERMINAL,
        ):
            break
        settled_prefix += 1
    return RunProgressSnapshot(
        run_id=run_id,
        progress=progress,
        settled_prefix=settled_prefix,
        succeeded_step_ids=tuple(succeeded),
        retryable_step_ids=tuple(by_class[OutcomeClass.RETRYABLE]),
        reconciliation_step_ids=tuple(by_class[OutcomeClass.RECONCILIATION_REQUIRED]),
        terminal_step_ids=tuple(terminal),
        pending_step_ids=tuple(pending),
    )


def list_repair_attention(
    db: Session, *, tenant_id: UUID, run_id: UUID | None = None
) -> tuple[RepairAttention, ...]:
    """Derive unresolved/uncertain repair work; no parallel queue is stored."""
    statement = (
        select(FulfillmentStep, FulfillmentAttempt, FulfillmentOutcomeReceipt)
        .join(
            FulfillmentAttempt,
            (FulfillmentAttempt.tenant_id == FulfillmentStep.tenant_id)
            & (FulfillmentAttempt.run_id == FulfillmentStep.run_id)
            & (FulfillmentAttempt.step_id == FulfillmentStep.id),
        )
        .outerjoin(
            FulfillmentOutcomeReceipt,
            (FulfillmentOutcomeReceipt.tenant_id == FulfillmentAttempt.tenant_id)
            & (FulfillmentOutcomeReceipt.attempt_id == FulfillmentAttempt.id),
        )
        .where(FulfillmentStep.tenant_id == tenant_id)
        .order_by(FulfillmentStep.run_id, FulfillmentStep.sequence)
    )
    if run_id is not None:
        statement = statement.where(FulfillmentStep.run_id == run_id)

    rows = tuple(db.execute(statement))
    latest: dict[UUID, tuple[FulfillmentStep, FulfillmentAttempt, object]] = {}
    for step, attempt, receipt in rows:
        current = latest.get(step.id)
        if current is None or attempt.sequence > current[1].sequence:
            latest[step.id] = (step, attempt, receipt)

    attention: list[RepairAttention] = []
    for step, attempt, raw_receipt in latest.values():
        receipt = (
            raw_receipt if isinstance(raw_receipt, FulfillmentOutcomeReceipt) else None
        )
        if receipt is not None and (
            receipt.classification == OutcomeClass.SUCCEEDED.value
            or receipt.reviewed_by_id is not None
        ):
            continue
        attention.append(
            RepairAttention(
                run_id=step.run_id,
                step_record_id=step.id,
                step_id=step.step_id,
                attempt_id=attempt.id,
                participant_code=step.participant_code,
                classification=(
                    OutcomeClass(receipt.classification)
                    if receipt is not None
                    else None
                ),
                reason_code=receipt.reason_code if receipt is not None else None,
            )
        )
    return tuple(attention)


def _compensation_command(
    step: FulfillmentStep,
    original: FulfillmentAttempt,
    request: FulfillmentCompensationRequest,
) -> CompensationCommand:
    return CompensationCommand(
        request_id=request.id,
        run_id=request.run_id,
        step_record_id=request.step_id,
        original_attempt_id=request.original_attempt_id,
        step_id=step.step_id,
        participant_code=request.participant_code,
        command_id=request.command_id,
        operation_id=request.operation_id,
        reason=request.reason,
        requested_at=request.requested_at,
    )


def request_compensation(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    request: CompensationRequest,
    publish: CompensationPublisher,
    actor: RepairActor,
    authorize: RepairAuthorizer,
) -> CompensationCommand:
    """Request compensation for the latest uncompensated success, in reverse order."""
    from dotmac_kernel.idempotency import execute_once, fingerprint_of

    request_fingerprint = fingerprint_of(
        {
            "run_id": str(run_id),
            "reason": request.reason,
            "requested_at": request.requested_at.isoformat(),
            "repair_actor": actor.as_fingerprint_payload(),
        }
    )

    def operation(session: Session) -> dict[str, object]:
        _run(session, tenant_id, run_id)
        authorize(
            session,
            tenant_id,
            actor,
            RepairAction.REQUEST_COMPENSATION,
        )
        open_request = session.scalar(
            select(FulfillmentCompensationRequest)
            .outerjoin(
                FulfillmentCompensationReceipt,
                (
                    FulfillmentCompensationReceipt.tenant_id
                    == FulfillmentCompensationRequest.tenant_id
                )
                & (
                    FulfillmentCompensationReceipt.request_id
                    == FulfillmentCompensationRequest.id
                ),
            )
            .where(
                FulfillmentCompensationRequest.tenant_id == tenant_id,
                FulfillmentCompensationRequest.run_id == run_id,
                FulfillmentCompensationReceipt.id.is_(None),
            )
            .limit(1)
        )
        if open_request is not None:
            raise FulfillmentConflict(
                "the previous compensation request has no participant receipt"
            )
        latest_compensation = session.execute(
            select(FulfillmentCompensationRequest, FulfillmentCompensationReceipt)
            .join(
                FulfillmentCompensationReceipt,
                (
                    FulfillmentCompensationReceipt.tenant_id
                    == FulfillmentCompensationRequest.tenant_id
                )
                & (
                    FulfillmentCompensationReceipt.request_id
                    == FulfillmentCompensationRequest.id
                ),
            )
            .where(
                FulfillmentCompensationRequest.tenant_id == tenant_id,
                FulfillmentCompensationRequest.run_id == run_id,
            )
            .order_by(FulfillmentCompensationRequest.sequence.desc())
            .limit(1)
        ).first()
        if (
            latest_compensation is not None
            and latest_compensation[1].disposition
            != CompensationDisposition.SUCCEEDED.value
        ):
            raise FulfillmentConflict(
                "the previous compensation did not succeed; repair it before "
                "compensating an earlier effect"
            )

        candidates = tuple(
            session.execute(
                select(FulfillmentStep, FulfillmentAttempt)
                .join(
                    FulfillmentAttempt,
                    (FulfillmentAttempt.tenant_id == FulfillmentStep.tenant_id)
                    & (FulfillmentAttempt.run_id == FulfillmentStep.run_id)
                    & (FulfillmentAttempt.step_id == FulfillmentStep.id),
                )
                .join(
                    FulfillmentOutcomeReceipt,
                    (
                        FulfillmentOutcomeReceipt.tenant_id
                        == FulfillmentAttempt.tenant_id
                    )
                    & (FulfillmentOutcomeReceipt.attempt_id == FulfillmentAttempt.id),
                )
                .outerjoin(
                    FulfillmentCompensationRequest,
                    (
                        FulfillmentCompensationRequest.tenant_id
                        == FulfillmentAttempt.tenant_id
                    )
                    & (
                        FulfillmentCompensationRequest.original_attempt_id
                        == FulfillmentAttempt.id
                    ),
                )
                .where(
                    FulfillmentStep.tenant_id == tenant_id,
                    FulfillmentStep.run_id == run_id,
                    FulfillmentOutcomeReceipt.classification
                    == OutcomeClass.SUCCEEDED.value,
                    FulfillmentCompensationRequest.id.is_(None),
                )
                .order_by(
                    FulfillmentOutcomeReceipt.recorded_at.desc(),
                    FulfillmentStep.sequence.desc(),
                )
            )
        )
        if not candidates:
            raise FulfillmentConflict("the run has no uncompensated successful effect")
        step, original = candidates[0]
        sequence = (
            int(
                session.scalar(
                    select(func.count(FulfillmentCompensationRequest.id)).where(
                        FulfillmentCompensationRequest.tenant_id == tenant_id,
                        FulfillmentCompensationRequest.run_id == run_id,
                    )
                )
                or 0
            )
            + 1
        )
        request_id = uuid4()
        row = FulfillmentCompensationRequest(
            id=request_id,
            tenant_id=tenant_id,
            run_id=run_id,
            step_id=step.id,
            original_attempt_id=original.id,
            sequence=sequence,
            participant_code=step.participant_code,
            command_id=f"compensate:{request_id}",
            operation_id=original.operation_id,
            idempotency_key=request.idempotency_key,
            reason=request.reason,
            requested_at=request.requested_at,
        )
        session.add(row)
        session.flush()
        publish(session, _compensation_command(step, original, row))
        _write_repair_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=RepairAction.REQUEST_COMPENSATION,
            run_id=run_id,
            step_id=step.step_id,
            details={
                "request_id": str(row.id),
                "original_attempt_id": str(original.id),
            },
        )
        return {"request_id": str(row.id)}

    try:
        outcome = execute_once(
            db,
            tenant_id=tenant_id,
            scope=_COMPENSATION_SCOPE,
            key=request.idempotency_key,
            operation=operation,
            operation_name="fulfillment.request_compensation",
            fingerprint=request_fingerprint,
        )
    except IntegrityError as exc:
        # `uq_fulfillment_comp_requests_attempt` is what refuses a second
        # compensation for one original attempt now that `_run` no longer
        # locks — see its docstring. Same translation the attempt path already
        # makes, so a concurrent caller is refused in this module's own
        # vocabulary rather than with a driver error.
        raise FulfillmentConflict(
            "the original attempt already has a compensation request"
        ) from exc
    row = db.scalar(
        select(FulfillmentCompensationRequest).where(
            FulfillmentCompensationRequest.tenant_id == tenant_id,
            FulfillmentCompensationRequest.id
            == UUID(str(outcome.result["request_id"])),
        )
    )
    if row is None:
        raise FulfillmentNotFound("recorded compensation request not found")
    step = db.scalar(
        select(FulfillmentStep).where(
            FulfillmentStep.tenant_id == tenant_id,
            FulfillmentStep.id == row.step_id,
        )
    )
    original = db.scalar(
        select(FulfillmentAttempt).where(
            FulfillmentAttempt.tenant_id == tenant_id,
            FulfillmentAttempt.id == row.original_attempt_id,
        )
    )
    if step is None or original is None:
        raise FulfillmentNotFound("compensation source evidence not found")
    return _compensation_command(step, original, row)


def record_compensation_outcome(
    db: Session,
    *,
    tenant_id: UUID,
    outcome: CompensationOutcome,
    schedule_reobservation: ReobservationScheduler | None = None,
) -> CompensationRecord:
    from dotmac_kernel.idempotency import execute_once, fingerprint_of

    request = db.scalar(
        select(FulfillmentCompensationRequest).where(
            FulfillmentCompensationRequest.tenant_id == tenant_id,
            FulfillmentCompensationRequest.command_id == outcome.command_id,
        )
    )
    if request is None:
        raise FulfillmentNotFound("compensation outcome has no matching request")
    if request.participant_code != outcome.participant_code:
        raise FulfillmentConflict("compensation outcome participant does not match")
    if (
        outcome.disposition
        in {
            CompensationDisposition.RETRYABLE,
            CompensationDisposition.RECONCILIATION_REQUIRED,
        }
        and schedule_reobservation is None
    ):
        raise FulfillmentConflict(
            "uncertain compensation outcomes require a durable-timer scheduler"
        )
    request_fingerprint = fingerprint_of(outcome.as_fingerprint_payload())

    def operation(session: Session) -> dict[str, object]:
        receipt = FulfillmentCompensationReceipt(
            tenant_id=tenant_id,
            request_id=request.id,
            outcome_id=outcome.outcome_id,
            participant_code=outcome.participant_code,
            command_id=outcome.command_id,
            request_fingerprint=request_fingerprint,
            disposition=outcome.disposition.value,
            reason_code=outcome.reason_code,
            detail=dict(outcome.detail),
            occurred_at=outcome.occurred_at,
        )
        session.add(receipt)
        session.flush()
        if schedule_reobservation is not None and outcome.reobserve_at is not None:
            schedule_reobservation(
                session,
                ReobservationSchedule(
                    run_id=request.run_id,
                    step_record_id=request.step_id,
                    attempt_id=request.original_attempt_id,
                    participant_code=request.participant_code,
                    operation_id=request.operation_id,
                    due_at=outcome.reobserve_at,
                ),
            )
        return {"receipt_id": str(receipt.id)}

    recorded = execute_once(
        db,
        tenant_id=tenant_id,
        scope=_COMPENSATION_OUTCOME_SCOPE,
        key=outcome.command_id,
        operation=operation,
        operation_name="fulfillment.record_compensation_outcome",
        fingerprint=request_fingerprint,
    )
    receipt = db.scalar(
        select(FulfillmentCompensationReceipt).where(
            FulfillmentCompensationReceipt.tenant_id == tenant_id,
            FulfillmentCompensationReceipt.id
            == UUID(str(recorded.result["receipt_id"])),
        )
    )
    if receipt is None:
        raise FulfillmentNotFound("recorded compensation outcome not found")
    return CompensationRecord(
        receipt_id=receipt.id,
        disposition=CompensationDisposition(receipt.disposition),
        replayed=recorded.replayed,
    )


__all__ = [
    "CommandPublisher",
    "CompensationPublisher",
    "ReobservationScheduler",
    "RepairAuthorizer",
    "create_run",
    "derive_run_progress",
    "list_repair_attention",
    "record_compensation_outcome",
    "record_outcome",
    "record_reviewed_terminal_outcome",
    "request_attempt",
    "request_compensation",
    "request_redrive",
]
