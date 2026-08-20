"""Persistence-owner behavior for reusable durable assets."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from dotmac_assets import (
    AssetCondition,
    AssetConflict,
    AssetCreate,
    AssetState,
    AssignmentCreate,
    AssignmentEnd,
    AssignmentStatus,
    AssignmentTransfer,
    DisposalApprove,
    DisposalComplete,
    DisposalMethod,
    DisposalRequest,
    DisposalStatus,
    MaintenanceComplete,
    MaintenanceKind,
    MaintenanceSchedule,
    MaintenanceStatus,
    StaleState,
    approve_disposal,
    assign_asset,
    complete_disposal,
    complete_maintenance,
    create_asset,
    create_asset_snapshot,
    end_assignment,
    move_asset,
    request_disposal,
    schedule_maintenance,
    start_maintenance,
    transfer_asset,
    transition_asset_state,
)
from dotmac_assets.models import (
    Asset,
    AssetAssignment,
    AssetDisposal,
    AssetLifecycleEvent,
    AssetMaintenance,
)
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_assets": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            Asset.__table__,
            AssetAssignment.__table__,
            AssetMaintenance.__table__,
            AssetDisposal.__table__,
            AssetLifecycleEvent.__table__,
        ],
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session) -> Tenant:
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(row)
    db.flush()
    return row


def _asset(db: Session, tenant_id, *, code: str = "AST-0001") -> Asset:
    return create_asset(
        db,
        tenant_id=tenant_id,
        request=AssetCreate(
            code=code,
            name="Core router",
            kind="network-equipment",
            serial_number=f"SN-{code}",
            condition=AssetCondition.GOOD,
            acquired_on=date(2026, 8, 1),
            actor_id=uuid4(),
        ),
    )


def test_asset_identity_is_tenant_local_and_creation_is_evidenced(db: Session) -> None:
    first_tenant = _tenant(db)
    second_tenant = _tenant(db)

    first = _asset(db, first_tenant.id)
    second = _asset(db, second_tenant.id)

    assert first.code == second.code == "AST-0001"
    assert first.state == AssetState.REGISTERED.value
    events = db.scalars(
        select(AssetLifecycleEvent).where(AssetLifecycleEvent.asset_id == first.id)
    ).all()
    assert [event.event_type for event in events] == ["asset_created"]


def test_asset_registration_exposes_an_immutable_network_handoff(db: Session) -> None:
    tenant = _tenant(db)

    snapshot = create_asset_snapshot(
        db,
        tenant_id=tenant.id,
        request=AssetCreate(
            code="AST-HANDOFF",
            name="Access switch",
            kind="network-equipment",
            serial_number="SWITCH-0001",
            source_ref="stock-issue:opaque-1",
        ),
    )

    assert snapshot.tenant_id == tenant.id
    assert snapshot.serial_number == "SWITCH-0001"
    assert snapshot.source_ref == "stock-issue:opaque-1"
    assert snapshot.state is AssetState.REGISTERED
    with pytest.raises(FrozenInstanceError):
        snapshot.code = "changed"  # type: ignore[misc]


def test_asset_rejects_a_future_acquisition_date(db: Session) -> None:
    tenant = _tenant(db)

    with pytest.raises(ValueError, match="cannot be in the future"):
        create_asset(
            db,
            tenant_id=tenant.id,
            request=AssetCreate(
                code="AST-FUTURE",
                name="Unreceived router",
                kind="network-equipment",
                acquired_on=date(2999, 1, 1),
            ),
        )


def test_assignment_has_one_active_owner_and_transfer_preserves_history(
    db: Session,
) -> None:
    tenant = _tenant(db)
    asset = _asset(db, tenant.id)
    first_holder = uuid4()
    second_holder = uuid4()
    actor = uuid4()
    first = assign_asset(
        db,
        tenant_id=tenant.id,
        request=AssignmentCreate(
            asset_id=asset.id,
            custodian_id=first_holder,
            starts_on=date(2026, 8, 2),
            condition_on_issue=AssetCondition.GOOD,
            actor_id=actor,
        ),
    )

    with pytest.raises(AssetConflict, match="already has an active assignment"):
        assign_asset(
            db,
            tenant_id=tenant.id,
            request=AssignmentCreate(
                asset_id=asset.id,
                custodian_id=second_holder,
                starts_on=date(2026, 8, 3),
                actor_id=actor,
            ),
        )

    second = transfer_asset(
        db,
        tenant_id=tenant.id,
        request=AssignmentTransfer(
            assignment_id=first.id,
            new_custodian_id=second_holder,
            transferred_on=date(2026, 8, 4),
            condition_on_issue=AssetCondition.GOOD,
            actor_id=actor,
        ),
    )

    assert first.status == AssignmentStatus.TRANSFERRED.value
    assert first.ended_on == date(2026, 8, 4)
    assert second.status == AssignmentStatus.ACTIVE.value
    assert second.preceding_assignment_id == first.id
    assert asset.state == AssetState.IN_SERVICE.value


def test_returning_custody_does_not_erase_the_assignment(db: Session) -> None:
    tenant = _tenant(db)
    asset = _asset(db, tenant.id)
    assignment = assign_asset(
        db,
        tenant_id=tenant.id,
        request=AssignmentCreate(
            asset_id=asset.id,
            custodian_id=uuid4(),
            starts_on=date(2026, 8, 2),
            actor_id=uuid4(),
        ),
    )

    returned = end_assignment(
        db,
        tenant_id=tenant.id,
        request=AssignmentEnd(
            assignment_id=assignment.id,
            expected=AssignmentStatus.ACTIVE,
            requested=AssignmentStatus.RETURNED,
            ended_on=date(2026, 8, 5),
            condition_on_return=AssetCondition.FAIR,
            actor_id=uuid4(),
        ),
    )

    assert returned is assignment
    assert returned.status == AssignmentStatus.RETURNED.value
    assert db.get(AssetAssignment, assignment.id) is assignment


def test_assignment_transfer_uses_the_atomic_transfer_command(db: Session) -> None:
    tenant = _tenant(db)
    asset = _asset(db, tenant.id)
    assignment = assign_asset(
        db,
        tenant_id=tenant.id,
        request=AssignmentCreate(
            asset_id=asset.id,
            custodian_id=uuid4(),
            starts_on=date(2026, 8, 2),
        ),
    )

    with pytest.raises(ValueError, match="returned or lost"):
        end_assignment(
            db,
            tenant_id=tenant.id,
            request=AssignmentEnd(
                assignment_id=assignment.id,
                expected=AssignmentStatus.ACTIVE,
                requested=AssignmentStatus.TRANSFERRED,
                ended_on=date(2026, 8, 3),
            ),
        )


def test_location_move_changes_only_the_opaque_location_projection(
    db: Session,
) -> None:
    tenant = _tenant(db)
    asset = _asset(db, tenant.id)
    destination = uuid4()

    moved = move_asset(
        db,
        tenant_id=tenant.id,
        asset_id=asset.id,
        location_id=destination,
        actor_id=uuid4(),
        occurred_at=datetime(2026, 8, 18, 10, tzinfo=UTC),
    )

    assert moved.location_id == destination
    event = db.scalars(
        select(AssetLifecycleEvent).where(
            AssetLifecycleEvent.asset_id == asset.id,
            AssetLifecycleEvent.event_type == "asset_location_changed",
        )
    ).first()
    assert event is not None
    assert event.event_type == "asset_location_changed"
    assert event.new_location_id == destination


def test_maintenance_owns_status_and_temporarily_takes_asset_out_of_service(
    db: Session,
) -> None:
    tenant = _tenant(db)
    asset = _asset(db, tenant.id)
    transition_asset_state(
        db,
        tenant_id=tenant.id,
        asset_id=asset.id,
        expected=AssetState.REGISTERED,
        requested=AssetState.IN_SERVICE,
        actor_id=uuid4(),
    )
    maintenance = schedule_maintenance(
        db,
        tenant_id=tenant.id,
        request=MaintenanceSchedule(
            asset_id=asset.id,
            kind=MaintenanceKind.PREVENTIVE,
            summary="Quarterly inspection",
            scheduled_for=date(2026, 8, 20),
            actor_id=uuid4(),
        ),
    )

    started = start_maintenance(
        db,
        tenant_id=tenant.id,
        maintenance_id=maintenance.id,
        expected=MaintenanceStatus.SCHEDULED,
        actor_id=uuid4(),
        started_at=datetime(2026, 8, 20, 8, tzinfo=UTC),
    )
    assert started.status == MaintenanceStatus.IN_PROGRESS.value
    assert asset.state == AssetState.OUT_OF_SERVICE.value

    completed = complete_maintenance(
        db,
        tenant_id=tenant.id,
        request=MaintenanceComplete(
            maintenance_id=maintenance.id,
            expected=MaintenanceStatus.IN_PROGRESS,
            completed_on=date(2026, 8, 20),
            work_performed="Inspected and cleaned",
            return_to_service=True,
            actor_id=uuid4(),
        ),
    )
    assert completed.status == MaintenanceStatus.COMPLETED.value
    assert asset.state == AssetState.IN_SERVICE.value


def test_only_one_maintenance_record_can_be_in_progress(db: Session) -> None:
    tenant = _tenant(db)
    asset = _asset(db, tenant.id)
    transition_asset_state(
        db,
        tenant_id=tenant.id,
        asset_id=asset.id,
        expected=AssetState.REGISTERED,
        requested=AssetState.IN_SERVICE,
        actor_id=uuid4(),
    )
    first = schedule_maintenance(
        db,
        tenant_id=tenant.id,
        request=MaintenanceSchedule(
            asset_id=asset.id,
            kind=MaintenanceKind.PREVENTIVE,
            summary="Inspect power supply",
            scheduled_for=date(2026, 8, 20),
        ),
    )
    second = schedule_maintenance(
        db,
        tenant_id=tenant.id,
        request=MaintenanceSchedule(
            asset_id=asset.id,
            kind=MaintenanceKind.CORRECTIVE,
            summary="Replace failed fan",
            scheduled_for=date(2026, 8, 20),
        ),
    )
    start_maintenance(
        db,
        tenant_id=tenant.id,
        maintenance_id=first.id,
        expected=MaintenanceStatus.SCHEDULED,
        actor_id=uuid4(),
    )

    with pytest.raises(AssetConflict, match="maintenance already in progress"):
        start_maintenance(
            db,
            tenant_id=tenant.id,
            maintenance_id=second.id,
            expected=MaintenanceStatus.SCHEDULED,
            actor_id=uuid4(),
        )


def test_disposal_requires_retirement_approval_and_separation_of_duties(
    db: Session,
) -> None:
    tenant = _tenant(db)
    asset = _asset(db, tenant.id)
    requester = uuid4()
    approver = uuid4()
    transition_asset_state(
        db,
        tenant_id=tenant.id,
        asset_id=asset.id,
        expected=AssetState.REGISTERED,
        requested=AssetState.RETIRED,
        actor_id=requester,
    )
    disposal = request_disposal(
        db,
        tenant_id=tenant.id,
        request=DisposalRequest(
            asset_id=asset.id,
            method=DisposalMethod.SCRAP,
            requested_on=date(2026, 8, 21),
            reason="Beyond economic repair",
            actor_id=requester,
        ),
    )

    approve_disposal(
        db,
        tenant_id=tenant.id,
        request=DisposalApprove(
            disposal_id=disposal.id,
            expected=DisposalStatus.REQUESTED,
            actor_id=approver,
            approved_at=datetime(2026, 8, 21, 12, tzinfo=UTC),
        ),
    )
    completed = complete_disposal(
        db,
        tenant_id=tenant.id,
        request=DisposalComplete(
            disposal_id=disposal.id,
            expected=DisposalStatus.APPROVED,
            disposed_on=date(2026, 8, 22),
            actor_id=approver,
            external_finance_ref="erp-journal:opaque-42",
        ),
    )

    assert completed.status == DisposalStatus.COMPLETED.value
    assert asset.state == AssetState.DISPOSED.value
    assert completed.external_finance_ref == "erp-journal:opaque-42"


def test_retirement_refuses_active_custody_and_stale_commands(db: Session) -> None:
    tenant = _tenant(db)
    asset = _asset(db, tenant.id)
    assign_asset(
        db,
        tenant_id=tenant.id,
        request=AssignmentCreate(
            asset_id=asset.id,
            custodian_id=uuid4(),
            starts_on=date(2026, 8, 2),
            actor_id=uuid4(),
        ),
    )

    with pytest.raises(AssetConflict, match="active assignment"):
        transition_asset_state(
            db,
            tenant_id=tenant.id,
            asset_id=asset.id,
            expected=AssetState.IN_SERVICE,
            requested=AssetState.RETIRED,
            actor_id=uuid4(),
        )

    with pytest.raises(StaleState):
        transition_asset_state(
            db,
            tenant_id=tenant.id,
            asset_id=asset.id,
            expected=AssetState.REGISTERED,
            requested=AssetState.OUT_OF_SERVICE,
            actor_id=uuid4(),
        )
