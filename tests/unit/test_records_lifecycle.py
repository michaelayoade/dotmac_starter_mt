"""Behavior canaries for declaration, retention, holds and disposition."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_kernel.models import Tenant
from dotmac_kernel.testing import create_test_engine, isolated_session
from dotmac_records import (
    ApprovalVerdict,
    CutoffRule,
    DeclareRecord,
    DefineRecordSeriesVersion,
    DefineRetentionScheduleVersion,
    DispositionBlocked,
    DispositionOutcome,
    FileSnapshot,
    FinalAction,
    HoldCaseDefinition,
    HoldTargetDefinition,
    PhysicalDeletionConfirmation,
    SourceSnapshot,
    TriggerConflict,
    TriggerObservation,
    approve_disposition_batch,
    authorize_destruction,
    confirm_destruction,
    create_disposition_batch,
    declare_record,
    define_record_series_version,
    define_retention_schedule_version,
    evaluate_disposition,
    observe_retention_trigger,
    open_hold_case,
    place_hold,
    release_hold_target,
)
from dotmac_records.models import ALL_MODELS
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def records_engine():
    engine = create_test_engine(
        tables=(Tenant.__table__, *(model.__table__ for model in ALL_MODELS))
    )
    yield engine
    engine.dispose()


@pytest.fixture
def records_db(records_engine) -> Iterator[Session]:
    with isolated_session(records_engine) as db:
        yield db


@pytest.fixture
def tenant_id(records_db: Session) -> uuid.UUID:
    tenant = Tenant(slug=f"records-{uuid.uuid4().hex[:8]}", name="Records")
    records_db.add(tenant)
    records_db.flush()
    return tenant.id


def _seed_policy(db: Session, tenant_id: uuid.UUID):
    actor = uuid.uuid4()
    schedule = define_retention_schedule_version(
        db,
        tenant_id=tenant_id,
        command=DefineRetentionScheduleVersion(
            schedule_code="EMP-SEPARATION",
            version=1,
            trigger_event_type="employee.separated.v1",
            duration_days=365 * 7,
            permanent=False,
            cutoff_rule=CutoffRule.CALENDAR_YEAR_END,
            final_action=FinalAction.DESTROY,
            disposition_approval_policy="records.destroy.four-eyes.v1",
            review_cadence_days=90,
            authority="Labour and tax obligations",
            accountable_owner="people.operations",
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    series = define_record_series_version(
        db,
        tenant_id=tenant_id,
        command=DefineRecordSeriesVersion(
            series_code="HR-EMPLOYEE",
            version=1,
            name="Employee files",
            parent_series_code=None,
            responsible_owner="people.operations",
            custodian="records.office",
            jurisdiction="NG",
            regulatory_basis="Employment and tax law",
            default_schedule_code=schedule.schedule_code,
            default_schedule_version=schedule.version,
            vital_record=False,
            confidentiality="restricted",
            transfer_destination="approved.archive",
            required_fields=("employee_ref",),
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    return series, schedule, actor


def _declaration(series, schedule, actor: uuid.UUID) -> DeclareRecord:
    return DeclareRecord(
        source=SourceSnapshot(
            owner="documents",
            source_type="document_version",
            source_id=str(uuid.uuid4()),
            source_version="1.0",
            authority="documents.version.created.v1",
            provenance={"document_code": "EMP-001"},
        ),
        file=FileSnapshot(
            file_id=uuid.uuid4(),
            checksum_sha256=f"sha256:{'a' * 64}",
            media_type="application/pdf",
            byte_length=4096,
        ),
        series_code=series.series_code,
        series_version=series.version,
        schedule_code=schedule.schedule_code,
        schedule_version=schedule.version,
        metadata={"employee_ref": "opaque-person-42"},
        sensitivity="restricted",
        access_restrictions=("records-custodian-only",),
        declared_by=actor,
        declared_at=NOW,
    )


def test_declaration_freezes_exact_source_file_series_and_schedule_versions(
    records_db: Session, tenant_id: uuid.UUID
) -> None:
    series, schedule, actor = _seed_policy(records_db, tenant_id)
    command = _declaration(series, schedule, actor)
    record = declare_record(records_db, tenant_id=tenant_id, command=command)
    replay = declare_record(records_db, tenant_id=tenant_id, command=command)
    assert replay.id == record.id
    assert record.source_owner == "documents"
    assert record.source_version == "1.0"
    assert record.file_id == command.file.file_id
    assert record.schedule_version == 1

    newer = define_retention_schedule_version(
        records_db,
        tenant_id=tenant_id,
        command=DefineRetentionScheduleVersion(
            schedule_code=schedule.schedule_code,
            version=2,
            trigger_event_type=schedule.trigger_event_type,
            duration_days=365 * 10,
            permanent=False,
            cutoff_rule=CutoffRule.EXACT_DATE,
            final_action=FinalAction.ARCHIVAL_REVIEW,
            disposition_approval_policy="records.archive.v1",
            review_cadence_days=180,
            authority="Revised authority",
            accountable_owner="records.office",
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    assert newer.version == 2
    assert record.schedule_version == 1


def test_trigger_observation_is_deduplicated_and_calculated_deterministically(
    records_db: Session, tenant_id: uuid.UUID
) -> None:
    series, schedule, actor = _seed_policy(records_db, tenant_id)
    record = declare_record(
        records_db,
        tenant_id=tenant_id,
        command=_declaration(series, schedule, actor),
    )
    observation = TriggerObservation(
        source_owner="people",
        source_event_id="employee-42-separated-1",
        source_fingerprint="b" * 64,
        event_type="employee.separated.v1",
        source_version="people.events.v1",
        occurred_at=datetime(2026, 2, 3, 12, 0, tzinfo=UTC),
        observed_at=NOW,
        provenance={"employee_ref": "opaque-person-42"},
    )
    first = observe_retention_trigger(
        records_db,
        tenant_id=tenant_id,
        record_id=record.id,
        observation=observation,
    )
    replay = observe_retention_trigger(
        records_db,
        tenant_id=tenant_id,
        record_id=record.id,
        observation=observation,
    )
    assert replay.observation_id == first.observation_id
    assert first.retention_due_at == datetime(2033, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert first.timer is not None
    assert first.timer.owner == "records"

    with pytest.raises(TriggerConflict):
        observe_retention_trigger(
            records_db,
            tenant_id=tenant_id,
            record_id=record.id,
            observation=TriggerObservation(
                **{
                    **observation.as_dict(),
                    "source_fingerprint": "c" * 64,
                }
            ),
        )


def test_multiple_holds_each_independently_override_disposition(
    records_db: Session, tenant_id: uuid.UUID
) -> None:
    series, schedule, actor = _seed_policy(records_db, tenant_id)
    record = declare_record(
        records_db,
        tenant_id=tenant_id,
        command=_declaration(series, schedule, actor),
    )
    observe_retention_trigger(
        records_db,
        tenant_id=tenant_id,
        record_id=record.id,
        observation=TriggerObservation(
            source_owner="people",
            source_event_id="separation",
            source_fingerprint="d" * 64,
            event_type="employee.separated.v1",
            source_version="v1",
            occurred_at=datetime(2010, 1, 1, tzinfo=UTC),
            observed_at=NOW,
            provenance={},
        ),
    )
    cases = [
        open_hold_case(
            records_db,
            tenant_id=tenant_id,
            command=HoldCaseDefinition(
                case_code=f"LIT-{index}",
                authority="Federal High Court",
                reason="Pending litigation",
                responsible_officer=actor,
                review_at=datetime(2026, 11, 1, tzinfo=UTC),
            ),
            recorded_at=NOW,
        )
        for index in (1, 2)
    ]
    targets = [
        place_hold(
            records_db,
            tenant_id=tenant_id,
            case_id=case.id,
            command=HoldTargetDefinition(record_id=record.id),
            actor_id=actor,
            recorded_at=NOW,
        )
        for case in cases
    ]
    blocked = evaluate_disposition(
        records_db,
        tenant_id=tenant_id,
        record_id=record.id,
        evaluated_at=NOW,
        source_state_fingerprint="source-still-closed",
        source_allows_disposition=True,
    )
    assert blocked.eligible is False
    assert blocked.active_hold_count == 2
    release_hold_target(
        records_db,
        tenant_id=tenant_id,
        target_id=targets[0].id,
        actor_id=actor,
        reason="First matter closed",
        recorded_at=NOW,
    )
    still_blocked = evaluate_disposition(
        records_db,
        tenant_id=tenant_id,
        record_id=record.id,
        evaluated_at=NOW,
        source_state_fingerprint="source-still-closed",
        source_allows_disposition=True,
    )
    assert still_blocked.active_hold_count == 1
    assert still_blocked.eligible is False


def test_records_authorizes_files_then_waits_for_physical_confirmation(
    records_db: Session, tenant_id: uuid.UUID
) -> None:
    series, schedule, actor = _seed_policy(records_db, tenant_id)
    command = _declaration(series, schedule, actor)
    record = declare_record(records_db, tenant_id=tenant_id, command=command)
    observe_retention_trigger(
        records_db,
        tenant_id=tenant_id,
        record_id=record.id,
        observation=TriggerObservation(
            source_owner="people",
            source_event_id="old-separation",
            source_fingerprint="e" * 64,
            event_type="employee.separated.v1",
            source_version="v1",
            occurred_at=datetime(2010, 1, 1, tzinfo=UTC),
            observed_at=NOW,
            provenance={},
        ),
    )
    evaluation = evaluate_disposition(
        records_db,
        tenant_id=tenant_id,
        record_id=record.id,
        evaluated_at=NOW,
        source_state_fingerprint="people:closed:e1",
        source_allows_disposition=True,
    )
    batch = create_disposition_batch(
        records_db,
        tenant_id=tenant_id,
        evaluations=(evaluation,),
        actor_id=actor,
        recorded_at=NOW,
    )
    with pytest.raises(DispositionBlocked, match="approval"):
        authorize_destruction(
            records_db,
            tenant_id=tenant_id,
            batch_id=batch.id,
            record_id=record.id,
            evaluated_at=NOW,
            source_state_fingerprint="people:closed:e1",
            source_allows_disposition=True,
        )
    with pytest.raises(DispositionBlocked, match="four-eye"):
        approve_disposition_batch(
            records_db,
            tenant_id=tenant_id,
            batch_id=batch.id,
            verdict=ApprovalVerdict(
                request_id=uuid.uuid4(),
                content_digest=batch.content_digest,
                approved=True,
                decided_at=NOW,
            ),
            actor_id=actor,
            recorded_at=NOW,
        )
    approve_disposition_batch(
        records_db,
        tenant_id=tenant_id,
        batch_id=batch.id,
        verdict=ApprovalVerdict(
            request_id=uuid.uuid4(),
            content_digest=batch.content_digest,
            approved=True,
            decided_at=NOW,
        ),
        actor_id=uuid.uuid4(),
        recorded_at=NOW,
    )
    authorization = authorize_destruction(
        records_db,
        tenant_id=tenant_id,
        batch_id=batch.id,
        record_id=record.id,
        evaluated_at=NOW,
        source_state_fingerprint="people:closed:e1",
        source_allows_disposition=True,
    )
    assert authorization.file_id == command.file.file_id
    assert authorization.outcome == DispositionOutcome.APPROVED_FOR_DESTRUCTION
    assert record.state == "disposition_authorized"
    confirm_destruction(
        records_db,
        tenant_id=tenant_id,
        confirmation=PhysicalDeletionConfirmation(
            authorization_id=authorization.authorization_id,
            file_id=authorization.file_id,
            checksum_sha256=authorization.checksum_sha256,
            physical_state="purged",
            confirmed_at=NOW,
            provider_evidence_ref="files:purge-receipt:opaque",
        ),
        actor_id=actor,
    )
    assert record.state == "destroyed"
