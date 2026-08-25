"""Records-owned retention, hold, preservation and disposition decisions.

The source domain remains authoritative for business state; Files remains
authoritative for physical state. This service consumes explicit observations
and returns authorizations/requests. It performs no external I/O and owns no
transaction boundary.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_records.contracts import (
    ApprovalVerdict,
    CutoffRule,
    DeclareRecord,
    DefineRecordSeriesVersion,
    DefineRetentionScheduleVersion,
    DeletionAuthorization,
    DispositionBlocked,
    DispositionEvaluation,
    DispositionOutcome,
    FinalAction,
    HoldCaseDefinition,
    HoldTargetDefinition,
    PhysicalDeletionConfirmation,
    RecordConflict,
    RecordNotFound,
    RecordsError,
    TimerRequest,
    TriggerConflict,
    TriggerObservation,
    TriggerResult,
)
from dotmac_records.models import (
    CustodyTransfer,
    DispositionBatch,
    DispositionItem,
    LegalHoldCase,
    LegalHoldTarget,
    PreservationCheck,
    Record,
    RecordEvent,
    RecordSeriesVersion,
    RecordTriggerObservation,
    RetentionScheduleVersion,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _digest(payload: object, *, prefixed: bool = False) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    value = hashlib.sha256(encoded).hexdigest()
    return f"sha256:{value}" if prefixed else value


def _schedule(
    db: Session, tenant_id: UUID, code: str, version: int
) -> RetentionScheduleVersion:
    row = db.scalar(
        select(RetentionScheduleVersion).where(
            RetentionScheduleVersion.tenant_id == tenant_id,
            RetentionScheduleVersion.schedule_code == code,
            RetentionScheduleVersion.version == version,
        )
    )
    if row is None:
        raise RecordNotFound(
            f"retention schedule {code!r} version {version} was not found"
        )
    return row


def _series(
    db: Session, tenant_id: UUID, code: str, version: int
) -> RecordSeriesVersion:
    row = db.scalar(
        select(RecordSeriesVersion).where(
            RecordSeriesVersion.tenant_id == tenant_id,
            RecordSeriesVersion.series_code == code,
            RecordSeriesVersion.version == version,
        )
    )
    if row is None:
        raise RecordNotFound(f"record series {code!r} version {version} was not found")
    return row


def _record(
    db: Session, tenant_id: UUID, record_id: UUID, *, lock: bool = False
) -> Record:
    statement = select(Record).where(
        Record.tenant_id == tenant_id, Record.id == record_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise RecordNotFound(f"record {record_id} was not found")
    return row


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    record_id: UUID,
    event_type: str,
    actor_id: UUID | None,
    payload: dict[str, object],
    occurred_at: datetime,
) -> RecordEvent:
    row = RecordEvent(
        tenant_id=tenant_id,
        record_id=record_id,
        event_type=event_type,
        actor_id=actor_id,
        payload=payload,
        occurred_at=occurred_at,
    )
    db.add(row)
    return row


def define_retention_schedule_version(
    db: Session,
    *,
    tenant_id: UUID,
    command: DefineRetentionScheduleVersion,
    actor_id: UUID,
    recorded_at: datetime,
) -> RetentionScheduleVersion:
    row = RetentionScheduleVersion(
        tenant_id=tenant_id,
        schedule_code=command.schedule_code,
        version=command.version,
        trigger_event_type=command.trigger_event_type,
        duration_days=command.duration_days,
        permanent=command.permanent,
        cutoff_rule=command.cutoff_rule.value,
        final_action=command.final_action.value,
        disposition_approval_policy=command.disposition_approval_policy,
        review_cadence_days=command.review_cadence_days,
        authority=command.authority,
        accountable_owner=command.accountable_owner,
        created_by=actor_id,
        created_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def define_record_series_version(
    db: Session,
    *,
    tenant_id: UUID,
    command: DefineRecordSeriesVersion,
    actor_id: UUID,
    recorded_at: datetime,
) -> RecordSeriesVersion:
    _schedule(
        db, tenant_id, command.default_schedule_code, command.default_schedule_version
    )
    if (
        command.parent_series_code is not None
        and command.parent_series_version is not None
    ):
        _series(
            db, tenant_id, command.parent_series_code, command.parent_series_version
        )
    row = RecordSeriesVersion(
        tenant_id=tenant_id,
        series_code=command.series_code,
        version=command.version,
        name=command.name,
        parent_series_code=command.parent_series_code,
        parent_series_version=command.parent_series_version,
        responsible_owner=command.responsible_owner,
        custodian=command.custodian,
        jurisdiction=command.jurisdiction,
        regulatory_basis=command.regulatory_basis,
        default_schedule_code=command.default_schedule_code,
        default_schedule_version=command.default_schedule_version,
        vital_record=command.vital_record,
        confidentiality=command.confidentiality,
        transfer_destination=command.transfer_destination,
        required_fields=list(command.required_fields),
        created_by=actor_id,
        created_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def _declaration_payload(command: DeclareRecord) -> dict[str, object]:
    file_payload: dict[str, object] | None = None
    if command.file is not None:
        file_payload = {
            "file_id": str(command.file.file_id),
            "checksum_sha256": command.file.checksum_sha256,
            "media_type": command.file.media_type,
            "byte_length": command.file.byte_length,
        }
    return {
        "source": {
            "owner": command.source.owner,
            "type": command.source.source_type,
            "id": command.source.source_id,
            "version": command.source.source_version,
            "authority": command.source.authority,
            "provenance": command.source.provenance,
        },
        "file": file_payload,
        "series": [command.series_code, command.series_version],
        "schedule": [command.schedule_code, command.schedule_version],
        "metadata": command.metadata,
        "sensitivity": command.sensitivity,
        "access_restrictions": command.access_restrictions,
        "declared_by": str(command.declared_by),
        "declared_at": command.declared_at,
        "supersedes_record_id": str(command.supersedes_record_id)
        if command.supersedes_record_id
        else None,
    }


def declare_record(db: Session, *, tenant_id: UUID, command: DeclareRecord) -> Record:
    series = _series(db, tenant_id, command.series_code, command.series_version)
    _schedule(db, tenant_id, command.schedule_code, command.schedule_version)
    missing = [name for name in series.required_fields if name not in command.metadata]
    if missing:
        raise RecordsError(f"required record metadata is missing: {', '.join(missing)}")
    if command.supersedes_record_id is not None:
        _record(db, tenant_id, command.supersedes_record_id)
    fingerprint = _digest(_declaration_payload(command))
    existing = db.scalar(
        select(Record).where(
            Record.tenant_id == tenant_id,
            Record.source_owner == command.source.owner,
            Record.source_type == command.source.source_type,
            Record.source_id == command.source.source_id,
            Record.source_version == command.source.source_version,
        )
    )
    if existing is not None:
        if existing.declaration_fingerprint != fingerprint:
            raise RecordConflict(
                "the exact source version was declared with different evidence"
            )
        return existing
    file_id = command.file.file_id if command.file else None
    checksum = command.file.checksum_sha256 if command.file else None
    media_type = command.file.media_type if command.file else None
    byte_length = command.file.byte_length if command.file else None
    row = Record(
        tenant_id=tenant_id,
        source_owner=command.source.owner,
        source_type=command.source.source_type,
        source_id=command.source.source_id,
        source_version=command.source.source_version,
        source_authority=command.source.authority,
        source_provenance=dict(command.source.provenance),
        file_id=file_id,
        checksum_sha256=checksum,
        media_type=media_type,
        byte_length=byte_length,
        series_code=command.series_code,
        series_version=command.series_version,
        schedule_code=command.schedule_code,
        schedule_version=command.schedule_version,
        record_metadata=dict(command.metadata),
        sensitivity=command.sensitivity,
        access_restrictions=list(command.access_restrictions),
        declaration_fingerprint=fingerprint,
        declared_by=command.declared_by,
        declared_at=command.declared_at,
        supersedes_record_id=command.supersedes_record_id,
        state="declared",
        updated_at=command.declared_at,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        record_id=row.id,
        event_type="record.declared",
        actor_id=command.declared_by,
        payload={
            "source_owner": row.source_owner,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "source_version": row.source_version,
            "file_id": str(row.file_id) if row.file_id else None,
            "checksum_sha256": row.checksum_sha256,
            "series": f"{row.series_code}:{row.series_version}",
            "schedule": f"{row.schedule_code}:{row.schedule_version}",
        },
        occurred_at=command.declared_at,
    )
    db.flush()
    return row


def _retention_due(
    schedule: RetentionScheduleVersion, occurred_at: datetime
) -> datetime | None:
    if schedule.permanent:
        return None
    if schedule.duration_days is None:
        raise RecordsError("timed retention schedule is missing duration_days")
    base = _as_utc(occurred_at) + timedelta(days=schedule.duration_days)
    cutoff = CutoffRule(schedule.cutoff_rule)
    if cutoff is CutoffRule.EXACT_DATE:
        return base
    if cutoff is CutoffRule.MONTH_END:
        day = calendar.monthrange(base.year, base.month)[1]
        return base.replace(day=day, hour=23, minute=59, second=59, microsecond=0)
    return base.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=0)


def observe_retention_trigger(
    db: Session,
    *,
    tenant_id: UUID,
    record_id: UUID,
    observation: TriggerObservation,
) -> TriggerResult:
    record = _record(db, tenant_id, record_id, lock=True)
    schedule = _schedule(db, tenant_id, record.schedule_code, record.schedule_version)
    if observation.event_type != schedule.trigger_event_type:
        raise TriggerConflict(
            f"schedule requires {schedule.trigger_event_type!r}, "
            f"not {observation.event_type!r}"
        )
    existing = db.scalar(
        select(RecordTriggerObservation).where(
            RecordTriggerObservation.tenant_id == tenant_id,
            RecordTriggerObservation.source_owner == observation.source_owner,
            RecordTriggerObservation.source_event_id == observation.source_event_id,
        )
    )
    if existing is not None:
        if (
            existing.source_fingerprint != observation.source_fingerprint
            or existing.record_id != record_id
        ):
            raise TriggerConflict(
                "source event identity was reused with different evidence"
            )
        review_at = (
            _as_utc(record.review_at)
            if record.review_at
            else _as_utc(observation.observed_at)
        )
        return TriggerResult(
            observation_id=existing.id,
            retention_due_at=_as_utc(record.retention_due_at)
            if record.retention_due_at
            else None,
            review_at=review_at,
            timer=TimerRequest(
                owner="records",
                entity_kind="record",
                entity_id=str(record.id),
                purpose="retention_review",
                due_at=review_at,
            ),
            replayed=True,
        )
    row = RecordTriggerObservation(
        tenant_id=tenant_id,
        record_id=record.id,
        source_owner=observation.source_owner,
        source_event_id=observation.source_event_id,
        source_fingerprint=observation.source_fingerprint,
        event_type=observation.event_type,
        source_version=observation.source_version,
        occurred_at=observation.occurred_at,
        observed_at=observation.observed_at,
        provenance=dict(observation.provenance),
    )
    db.add(row)
    db.flush()
    due = _retention_due(schedule, observation.occurred_at)
    cadence_review = _as_utc(observation.observed_at) + timedelta(
        days=schedule.review_cadence_days
    )
    review_at = min(due, cadence_review) if due is not None else cadence_review
    if record.retention_triggered_at is None:
        record.retention_triggered_at = observation.occurred_at
        record.retention_due_at = due
        record.review_at = review_at
        record.state = "retention_running"
        record.updated_at = observation.observed_at
    _event(
        db,
        tenant_id=tenant_id,
        record_id=record.id,
        event_type="record.retention_trigger_observed",
        actor_id=None,
        payload={
            "source_owner": observation.source_owner,
            "source_event_id": observation.source_event_id,
            "source_fingerprint": observation.source_fingerprint,
            "event_type": observation.event_type,
            "retention_due_at": due.isoformat() if due else None,
            "review_at": review_at.isoformat(),
        },
        occurred_at=observation.observed_at,
    )
    db.flush()
    return TriggerResult(
        observation_id=row.id,
        retention_due_at=due,
        review_at=review_at,
        timer=TimerRequest(
            owner="records",
            entity_kind="record",
            entity_id=str(record.id),
            purpose="retention_review",
            due_at=review_at,
        ),
        replayed=False,
    )


def open_hold_case(
    db: Session,
    *,
    tenant_id: UUID,
    command: HoldCaseDefinition,
    recorded_at: datetime,
) -> LegalHoldCase:
    row = LegalHoldCase(
        tenant_id=tenant_id,
        case_code=command.case_code,
        authority=command.authority,
        reason=command.reason,
        responsible_officer=command.responsible_officer,
        status="open",
        ongoing_capture_rule=(
            dict(command.ongoing_capture_rule) if command.ongoing_capture_rule else None
        ),
        opened_at=recorded_at,
        review_at=command.review_at,
    )
    db.add(row)
    db.flush()
    return row


def place_hold(
    db: Session,
    *,
    tenant_id: UUID,
    case_id: UUID,
    command: HoldTargetDefinition,
    actor_id: UUID,
    recorded_at: datetime,
) -> LegalHoldTarget:
    case = db.scalar(
        select(LegalHoldCase)
        .where(LegalHoldCase.tenant_id == tenant_id, LegalHoldCase.id == case_id)
        .with_for_update()
    )
    if case is None or case.status != "open":
        raise RecordNotFound(f"open legal hold case {case_id} was not found")
    if command.record_id is not None:
        _record(db, tenant_id, command.record_id)
        target_kind = "record"
    elif command.series_code is not None:
        _series(db, tenant_id, command.series_code, command.series_version or 0)
        target_kind = "series"
    else:
        target_kind = "cohort"
    row = LegalHoldTarget(
        tenant_id=tenant_id,
        case_id=case_id,
        target_kind=target_kind,
        record_id=command.record_id,
        series_code=command.series_code,
        series_version=command.series_version,
        cohort_fingerprint=command.cohort_fingerprint,
        cohort_snapshot=dict(command.cohort_snapshot)
        if command.cohort_snapshot
        else None,
        placed_by=actor_id,
        placed_at=recorded_at,
    )
    db.add(row)
    if command.record_id is not None:
        _event(
            db,
            tenant_id=tenant_id,
            record_id=command.record_id,
            event_type="record.hold_placed",
            actor_id=actor_id,
            payload={"case_id": str(case_id), "target_kind": target_kind},
            occurred_at=recorded_at,
        )
    db.flush()
    return row


def release_hold_target(
    db: Session,
    *,
    tenant_id: UUID,
    target_id: UUID,
    actor_id: UUID,
    reason: str,
    recorded_at: datetime,
) -> LegalHoldTarget:
    row = db.scalar(
        select(LegalHoldTarget)
        .where(LegalHoldTarget.tenant_id == tenant_id, LegalHoldTarget.id == target_id)
        .with_for_update()
    )
    if row is None:
        raise RecordNotFound(f"legal hold target {target_id} was not found")
    if row.released_at is None:
        row.released_at = recorded_at
        row.released_by = actor_id
        row.release_reason = reason
        if row.record_id is not None:
            _event(
                db,
                tenant_id=tenant_id,
                record_id=row.record_id,
                event_type="record.hold_released",
                actor_id=actor_id,
                payload={"case_id": str(row.case_id), "target_id": str(row.id)},
                occurred_at=recorded_at,
            )
        db.flush()
    return row


def _active_hold_count(db: Session, tenant_id: UUID, record: Record) -> int:
    open_cases = set(
        db.scalars(
            select(LegalHoldCase.id).where(
                LegalHoldCase.tenant_id == tenant_id, LegalHoldCase.status == "open"
            )
        )
    )
    targets = db.scalars(
        select(LegalHoldTarget).where(
            LegalHoldTarget.tenant_id == tenant_id,
            LegalHoldTarget.released_at.is_(None),
        )
    )
    count = 0
    for target in targets:
        if target.case_id not in open_cases:
            continue
        applies = target.record_id == record.id
        applies = applies or (
            target.series_code == record.series_code
            and target.series_version == record.series_version
        )
        if target.cohort_snapshot:
            raw_record_ids = target.cohort_snapshot.get("record_ids", [])
            record_ids = raw_record_ids if isinstance(raw_record_ids, list) else []
            applies = applies or str(record.id) in record_ids
        if applies:
            count += 1
    return count


def evaluate_disposition(
    db: Session,
    *,
    tenant_id: UUID,
    record_id: UUID,
    evaluated_at: datetime,
    source_state_fingerprint: str,
    source_allows_disposition: bool,
) -> DispositionEvaluation:
    record = _record(db, tenant_id, record_id)
    schedule = _schedule(db, tenant_id, record.schedule_code, record.schedule_version)
    holds = _active_hold_count(db, tenant_id, record)
    due = _as_utc(record.retention_due_at) if record.retention_due_at else None
    eligible = True
    reason = "eligible"
    if record.state in {"destroyed", "transferred", "retained_permanently"}:
        eligible, reason = False, "record is already in a final state"
    elif holds:
        eligible, reason = False, "one or more legal holds are active"
    elif not source_allows_disposition:
        eligible, reason = False, "authoritative source state blocks disposition"
    elif (
        schedule.permanent
        and schedule.final_action == FinalAction.RETAIN_PERMANENTLY.value
    ):
        eligible, reason = True, "permanent retention action is due for recording"
    elif due is None:
        eligible, reason = False, "retention trigger has not established a due date"
    elif due > _as_utc(evaluated_at):
        eligible, reason = False, "retention period has not expired"
    payload = {
        "record_id": str(record.id),
        "declaration_fingerprint": record.declaration_fingerprint,
        "schedule": [record.schedule_code, record.schedule_version],
        "due": due,
        "holds": holds,
        "source_state_fingerprint": source_state_fingerprint,
        "source_allows_disposition": source_allows_disposition,
        "final_action": schedule.final_action,
        "eligible": eligible,
        "reason": reason,
    }
    return DispositionEvaluation(
        record_id=record.id,
        eligible=eligible,
        reason=reason,
        active_hold_count=holds,
        final_action=FinalAction(schedule.final_action),
        source_state_fingerprint=source_state_fingerprint,
        eligibility_fingerprint=_digest(payload),
        evaluated_at=evaluated_at,
    )


def create_disposition_batch(
    db: Session,
    *,
    tenant_id: UUID,
    evaluations: tuple[DispositionEvaluation, ...],
    actor_id: UUID,
    recorded_at: datetime,
) -> DispositionBatch:
    if not evaluations or any(not item.eligible for item in evaluations):
        raise DispositionBlocked("a disposition batch accepts only eligible records")
    ordered = sorted(evaluations, key=lambda item: str(item.record_id))
    content_digest = _digest(
        [
            [str(item.record_id), item.eligibility_fingerprint, item.final_action.value]
            for item in ordered
        ],
        prefixed=True,
    )
    existing = db.scalar(
        select(DispositionBatch).where(
            DispositionBatch.tenant_id == tenant_id,
            DispositionBatch.content_digest == content_digest,
        )
    )
    if existing is not None:
        return existing
    batch = DispositionBatch(
        tenant_id=tenant_id,
        content_digest=content_digest,
        status="pending_approval",
        created_by=actor_id,
        created_at=recorded_at,
    )
    db.add(batch)
    db.flush()
    for evaluation in ordered:
        db.add(
            DispositionItem(
                tenant_id=tenant_id,
                batch_id=batch.id,
                record_id=evaluation.record_id,
                eligibility_fingerprint=evaluation.eligibility_fingerprint,
                eligibility_snapshot={
                    "evaluated_at": evaluation.evaluated_at.isoformat(),
                    "source_state_fingerprint": evaluation.source_state_fingerprint,
                    "active_hold_count": evaluation.active_hold_count,
                    "reason": evaluation.reason,
                },
                final_action=evaluation.final_action.value,
                status="pending_approval",
            )
        )
    db.flush()
    return batch


def approve_disposition_batch(
    db: Session,
    *,
    tenant_id: UUID,
    batch_id: UUID,
    verdict: ApprovalVerdict,
    actor_id: UUID,
    recorded_at: datetime,
) -> DispositionBatch:
    batch = db.scalar(
        select(DispositionBatch)
        .where(DispositionBatch.tenant_id == tenant_id, DispositionBatch.id == batch_id)
        .with_for_update()
    )
    if batch is None:
        raise RecordNotFound(f"disposition batch {batch_id} was not found")
    if not verdict.approved:
        batch.status = "refused"
    elif verdict.content_digest != batch.content_digest:
        raise DispositionBlocked(
            "approval digest does not match disposition batch content"
        )
    else:
        items = list(
            db.scalars(
                select(DispositionItem).where(
                    DispositionItem.tenant_id == tenant_id,
                    DispositionItem.batch_id == batch_id,
                )
            )
        )
        four_eye_required = False
        for item in items:
            record = _record(db, tenant_id, item.record_id)
            schedule = _schedule(
                db, tenant_id, record.schedule_code, record.schedule_version
            )
            if "four-eye" in schedule.disposition_approval_policy:
                four_eye_required = True
                break
        if four_eye_required and actor_id == batch.created_by:
            raise DispositionBlocked(
                "four-eye policy forbids the batch creator from approving"
            )
        batch.status = "approved"
    batch.approval_request_id = verdict.request_id
    batch.approval_digest = verdict.content_digest
    batch.approved_at = verdict.decided_at
    batch.approved_by = actor_id
    db.flush()
    return batch


def authorize_destruction(
    db: Session,
    *,
    tenant_id: UUID,
    batch_id: UUID,
    record_id: UUID,
    evaluated_at: datetime,
    source_state_fingerprint: str,
    source_allows_disposition: bool,
) -> DeletionAuthorization:
    batch = db.scalar(
        select(DispositionBatch)
        .where(DispositionBatch.tenant_id == tenant_id, DispositionBatch.id == batch_id)
        .with_for_update()
    )
    if batch is None or batch.status != "approved":
        raise DispositionBlocked("disposition batch approval is required")
    item = db.scalar(
        select(DispositionItem)
        .where(
            DispositionItem.tenant_id == tenant_id,
            DispositionItem.batch_id == batch_id,
            DispositionItem.record_id == record_id,
        )
        .with_for_update()
    )
    if item is None:
        raise RecordNotFound(f"record {record_id} is not a member of batch {batch_id}")
    record = _record(db, tenant_id, record_id, lock=True)
    if item.authorization_id is not None:
        if (
            record.file_id is None
            or record.checksum_sha256 is None
            or item.authorized_at is None
        ):
            raise DispositionBlocked("stored authorization is incomplete")
        return DeletionAuthorization(
            authorization_id=item.authorization_id,
            record_id=record.id,
            file_id=record.file_id,
            checksum_sha256=record.checksum_sha256,
            outcome=DispositionOutcome.APPROVED_FOR_DESTRUCTION,
            authorized_at=_as_utc(item.authorized_at),
        )
    evaluation = evaluate_disposition(
        db,
        tenant_id=tenant_id,
        record_id=record_id,
        evaluated_at=evaluated_at,
        source_state_fingerprint=source_state_fingerprint,
        source_allows_disposition=source_allows_disposition,
    )
    if not evaluation.eligible:
        raise DispositionBlocked(
            f"conditional disposition recheck failed: {evaluation.reason}"
        )
    if evaluation.eligibility_fingerprint != item.eligibility_fingerprint:
        raise DispositionBlocked(
            "conditional disposition evidence changed after batch creation"
        )
    if item.final_action != FinalAction.DESTROY.value:
        raise DispositionBlocked(
            "this record's final action is not physical destruction"
        )
    if record.file_id is None or record.checksum_sha256 is None:
        raise DispositionBlocked("record has no physical content to destroy")
    authorization_id = uuid4()
    item.authorization_id = authorization_id
    item.authorized_at = evaluated_at
    item.status = "authorized"
    item.outcome = DispositionOutcome.APPROVED_FOR_DESTRUCTION.value
    record.state = "disposition_authorized"
    record.updated_at = evaluated_at
    _event(
        db,
        tenant_id=tenant_id,
        record_id=record.id,
        event_type="record.destruction_authorized",
        actor_id=None,
        payload={
            "batch_id": str(batch.id),
            "authorization_id": str(authorization_id),
            "file_id": str(record.file_id),
            "checksum_sha256": record.checksum_sha256,
        },
        occurred_at=evaluated_at,
    )
    db.flush()
    return DeletionAuthorization(
        authorization_id=authorization_id,
        record_id=record.id,
        file_id=record.file_id,
        checksum_sha256=record.checksum_sha256,
        outcome=DispositionOutcome.APPROVED_FOR_DESTRUCTION,
        authorized_at=evaluated_at,
    )


def confirm_destruction(
    db: Session,
    *,
    tenant_id: UUID,
    confirmation: PhysicalDeletionConfirmation,
    actor_id: UUID,
) -> Record:
    item = db.scalar(
        select(DispositionItem)
        .where(
            DispositionItem.tenant_id == tenant_id,
            DispositionItem.authorization_id == confirmation.authorization_id,
        )
        .with_for_update()
    )
    if item is None:
        raise RecordNotFound(
            f"destruction authorization {confirmation.authorization_id} was not found"
        )
    record = _record(db, tenant_id, item.record_id, lock=True)
    if (
        record.file_id != confirmation.file_id
        or record.checksum_sha256 != confirmation.checksum_sha256
    ):
        raise DispositionBlocked(
            "physical confirmation does not identify the authorized content"
        )
    if confirmation.physical_state != "purged":
        raise DispositionBlocked("Files has not confirmed the purged physical state")
    if item.executed_at is None:
        item.status = "executed"
        item.executed_at = confirmation.confirmed_at
        item.physical_state = confirmation.physical_state
        item.provider_evidence_ref = confirmation.provider_evidence_ref
        record.state = "destroyed"
        record.final_evidence_ref = confirmation.provider_evidence_ref
        record.updated_at = confirmation.confirmed_at
        _event(
            db,
            tenant_id=tenant_id,
            record_id=record.id,
            event_type="record.destroyed",
            actor_id=actor_id,
            payload={
                "authorization_id": str(confirmation.authorization_id),
                "checksum_sha256": confirmation.checksum_sha256,
                "physical_state": confirmation.physical_state,
                "provider_evidence_ref": confirmation.provider_evidence_ref,
            },
            occurred_at=confirmation.confirmed_at,
        )
        db.flush()
    return record


def record_preservation_check(
    db: Session,
    *,
    tenant_id: UUID,
    record_id: UUID,
    source_owner: str,
    source_observation_id: str,
    observed_checksum_sha256: str | None,
    physical_state: str,
    storage_location_observation: str | None,
    evidence: dict[str, object],
    checked_at: datetime,
) -> PreservationCheck:
    record = _record(db, tenant_id, record_id)
    if record.checksum_sha256 is None:
        raise RecordsError("a byte-free record has no fixity check")
    row = PreservationCheck(
        tenant_id=tenant_id,
        record_id=record.id,
        source_owner=source_owner,
        source_observation_id=source_observation_id,
        expected_checksum_sha256=record.checksum_sha256,
        observed_checksum_sha256=observed_checksum_sha256,
        physical_state=physical_state,
        storage_location_observation=storage_location_observation,
        checked_at=checked_at,
        evidence=dict(evidence),
    )
    db.add(row)
    db.flush()
    return row


def record_custody_transfer(
    db: Session,
    *,
    tenant_id: UUID,
    record_id: UUID,
    from_custodian: str,
    to_custodian: str,
    manifest_fingerprint: str,
    manifest_file_id: UUID | None,
    manifest_checksum_sha256: str | None,
    actor_id: UUID,
    transferred_at: datetime,
) -> CustodyTransfer:
    _record(db, tenant_id, record_id)
    row = CustodyTransfer(
        tenant_id=tenant_id,
        record_id=record_id,
        from_custodian=from_custodian,
        to_custodian=to_custodian,
        manifest_fingerprint=manifest_fingerprint,
        manifest_file_id=manifest_file_id,
        manifest_checksum_sha256=manifest_checksum_sha256,
        transferred_by=actor_id,
        transferred_at=transferred_at,
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "approve_disposition_batch",
    "authorize_destruction",
    "confirm_destruction",
    "create_disposition_batch",
    "declare_record",
    "define_record_series_version",
    "define_retention_schedule_version",
    "evaluate_disposition",
    "observe_retention_trigger",
    "open_hold_case",
    "place_hold",
    "record_custody_transfer",
    "record_preservation_check",
    "release_hold_target",
]
