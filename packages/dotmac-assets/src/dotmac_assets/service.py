"""Flush-only persistence owner for individual durable assets."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_assets.contracts import (
    AssetCondition,
    AssetCreate,
    AssetSnapshot,
    AssetState,
    AssignmentCreate,
    AssignmentEnd,
    AssignmentStatus,
    AssignmentTransfer,
    DisposalApprove,
    DisposalCancel,
    DisposalComplete,
    DisposalRequest,
    DisposalStatus,
    MaintenanceCancel,
    MaintenanceComplete,
    MaintenanceSchedule,
    MaintenanceStatus,
)
from dotmac_assets.lifecycle import (
    transition_asset,
    transition_assignment,
    transition_disposal,
    transition_maintenance,
)
from dotmac_assets.models import (
    Asset,
    AssetAssignment,
    AssetDisposal,
    AssetLifecycleEvent,
    AssetMaintenance,
)


class AssetNotFound(LookupError):
    """The requested aggregate member is absent from this tenant."""


class AssetConflict(ValueError):
    """The command conflicts with authoritative asset state."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must not be blank")
    return cleaned


def _optional_clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _asset(db: Session, tenant_id: UUID, asset_id: UUID) -> Asset:
    row = db.scalar(
        select(Asset)
        .where(Asset.tenant_id == tenant_id, Asset.id == asset_id)
        .with_for_update()
    )
    if row is None:
        raise AssetNotFound("asset not found")
    return row


def _assignment_scope(
    db: Session, tenant_id: UUID, assignment_id: UUID
) -> tuple[Asset, AssetAssignment]:
    asset_id = db.scalar(
        select(AssetAssignment.asset_id).where(
            AssetAssignment.tenant_id == tenant_id,
            AssetAssignment.id == assignment_id,
        )
    )
    if asset_id is None:
        raise AssetNotFound("asset assignment not found")
    asset = _asset(db, tenant_id, asset_id)
    assignment = db.scalar(
        select(AssetAssignment)
        .where(
            AssetAssignment.tenant_id == tenant_id,
            AssetAssignment.id == assignment_id,
        )
        .with_for_update()
    )
    if assignment is None or assignment.asset_id != asset.id:
        raise AssetConflict("assignment changed while acquiring aggregate locks")
    return asset, assignment


def _maintenance_scope(
    db: Session, tenant_id: UUID, maintenance_id: UUID
) -> tuple[Asset, AssetMaintenance]:
    asset_id = db.scalar(
        select(AssetMaintenance.asset_id).where(
            AssetMaintenance.tenant_id == tenant_id,
            AssetMaintenance.id == maintenance_id,
        )
    )
    if asset_id is None:
        raise AssetNotFound("asset maintenance record not found")
    asset = _asset(db, tenant_id, asset_id)
    maintenance = db.scalar(
        select(AssetMaintenance)
        .where(
            AssetMaintenance.tenant_id == tenant_id,
            AssetMaintenance.id == maintenance_id,
        )
        .with_for_update()
    )
    if maintenance is None or maintenance.asset_id != asset.id:
        raise AssetConflict("maintenance changed while acquiring aggregate locks")
    return asset, maintenance


def _disposal_scope(
    db: Session, tenant_id: UUID, disposal_id: UUID
) -> tuple[Asset, AssetDisposal]:
    asset_id = db.scalar(
        select(AssetDisposal.asset_id).where(
            AssetDisposal.tenant_id == tenant_id,
            AssetDisposal.id == disposal_id,
        )
    )
    if asset_id is None:
        raise AssetNotFound("asset disposal not found")
    asset = _asset(db, tenant_id, asset_id)
    disposal = db.scalar(
        select(AssetDisposal)
        .where(
            AssetDisposal.tenant_id == tenant_id,
            AssetDisposal.id == disposal_id,
        )
        .with_for_update()
    )
    if disposal is None or disposal.asset_id != asset.id:
        raise AssetConflict("disposal changed while acquiring aggregate locks")
    return asset, disposal


def _active_assignment(
    db: Session, tenant_id: UUID, asset_id: UUID
) -> AssetAssignment | None:
    return db.scalar(
        select(AssetAssignment)
        .where(
            AssetAssignment.tenant_id == tenant_id,
            AssetAssignment.asset_id == asset_id,
            AssetAssignment.status == AssignmentStatus.ACTIVE.value,
        )
        .with_for_update()
    )


def _open_maintenance(
    db: Session, tenant_id: UUID, asset_id: UUID
) -> AssetMaintenance | None:
    return db.scalar(
        select(AssetMaintenance)
        .where(
            AssetMaintenance.tenant_id == tenant_id,
            AssetMaintenance.asset_id == asset_id,
            AssetMaintenance.status.in_(
                (
                    MaintenanceStatus.SCHEDULED.value,
                    MaintenanceStatus.IN_PROGRESS.value,
                )
            ),
        )
        .with_for_update()
        .limit(1)
    )


def _in_progress_maintenance(
    db: Session,
    tenant_id: UUID,
    asset_id: UUID,
    *,
    excluding_id: UUID,
) -> AssetMaintenance | None:
    return db.scalar(
        select(AssetMaintenance)
        .where(
            AssetMaintenance.tenant_id == tenant_id,
            AssetMaintenance.asset_id == asset_id,
            AssetMaintenance.id != excluding_id,
            AssetMaintenance.status == MaintenanceStatus.IN_PROGRESS.value,
        )
        .with_for_update()
        .limit(1)
    )


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    event_type: str,
    actor_id: UUID | None,
    source_id: UUID | None = None,
    occurred_at: datetime | None = None,
    previous_state: str | None = None,
    new_state: str | None = None,
    previous_custodian_id: UUID | None = None,
    new_custodian_id: UUID | None = None,
    previous_location_id: UUID | None = None,
    new_location_id: UUID | None = None,
    notes: str | None = None,
) -> AssetLifecycleEvent:
    row = AssetLifecycleEvent(
        tenant_id=tenant_id,
        asset_id=asset_id,
        event_type=event_type,
        occurred_at=occurred_at or datetime.now(UTC),
        actor_id=actor_id,
        source_id=source_id,
        previous_state=previous_state,
        new_state=new_state,
        previous_custodian_id=previous_custodian_id,
        new_custodian_id=new_custodian_id,
        previous_location_id=previous_location_id,
        new_location_id=new_location_id,
        notes=_optional_clean(notes),
    )
    db.add(row)
    return row


def _retirement_ready(db: Session, tenant_id: UUID, asset_id: UUID) -> None:
    if _active_assignment(db, tenant_id, asset_id) is not None:
        raise AssetConflict("asset has an active assignment")
    if _open_maintenance(db, tenant_id, asset_id) is not None:
        raise AssetConflict("asset has open maintenance")


def create_asset(db: Session, *, tenant_id: UUID, request: AssetCreate) -> Asset:
    if request.acquired_on is not None and request.acquired_on > date.today():
        raise ValueError("asset acquisition date cannot be in the future")
    row = Asset(
        tenant_id=tenant_id,
        code=_clean(request.code, "asset code"),
        name=_clean(request.name, "asset name"),
        kind=_clean(request.kind, "asset kind"),
        description=_optional_clean(request.description),
        serial_number=_optional_clean(request.serial_number),
        tag=_optional_clean(request.tag),
        manufacturer=_optional_clean(request.manufacturer),
        model=_optional_clean(request.model),
        acquired_on=request.acquired_on,
        state=AssetState.REGISTERED.value,
        condition=request.condition.value,
        location_id=request.location_id,
        source_ref=_optional_clean(request.source_ref),
        created_by_id=request.actor_id,
    )
    # Lazy by design: package discovery must not construct configured engines.
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                asset_id=row.id,
                event_type="asset_created",
                actor_id=request.actor_id,
                source_id=row.id,
                new_state=row.state,
                new_location_id=row.location_id,
            )
            db.flush()
    except IntegrityError as exc:
        raise AssetConflict(
            "asset code, serial number, or tag already exists for this tenant"
        ) from exc
    return row


def create_asset_snapshot(
    db: Session, *, tenant_id: UUID, request: AssetCreate
) -> AssetSnapshot:
    """Register a durable unit and return the network-suite handoff contract."""
    row = create_asset(db, tenant_id=tenant_id, request=request)
    return AssetSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        name=row.name,
        kind=row.kind,
        description=row.description,
        serial_number=row.serial_number,
        tag=row.tag,
        manufacturer=row.manufacturer,
        model=row.model,
        acquired_on=row.acquired_on,
        state=AssetState(row.state),
        condition=AssetCondition(row.condition),
        location_id=row.location_id,
        source_ref=row.source_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def transition_asset_state(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    expected: AssetState,
    requested: AssetState,
    actor_id: UUID | None,
    notes: str | None = None,
    occurred_at: datetime | None = None,
) -> Asset:
    asset = _asset(db, tenant_id, asset_id)
    current = AssetState(asset.state)
    if requested is AssetState.RETIRED:
        _retirement_ready(db, tenant_id, asset_id)
    if (
        requested is AssetState.IN_SERVICE
        and current is AssetState.OUT_OF_SERVICE
        and _open_maintenance(db, tenant_id, asset_id) is not None
    ):
        raise AssetConflict("asset has open maintenance")
    asset.state = transition_asset(current, requested, expected=expected).value
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="asset_state_changed",
        actor_id=actor_id,
        source_id=asset.id,
        occurred_at=occurred_at,
        previous_state=current.value,
        new_state=asset.state,
        notes=notes,
    )
    db.flush()
    return asset


def move_asset(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    location_id: UUID | None,
    actor_id: UUID | None,
    occurred_at: datetime | None = None,
    notes: str | None = None,
) -> Asset:
    asset = _asset(db, tenant_id, asset_id)
    if AssetState(asset.state) is AssetState.DISPOSED:
        raise AssetConflict("disposed asset location cannot change")
    previous = asset.location_id
    if previous == location_id:
        return asset
    asset.location_id = location_id
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="asset_location_changed",
        actor_id=actor_id,
        source_id=asset.id,
        occurred_at=occurred_at,
        previous_location_id=previous,
        new_location_id=location_id,
        notes=notes,
    )
    db.flush()
    return asset


def assign_asset(
    db: Session, *, tenant_id: UUID, request: AssignmentCreate
) -> AssetAssignment:
    asset = _asset(db, tenant_id, request.asset_id)
    state = AssetState(asset.state)
    if state not in {AssetState.REGISTERED, AssetState.IN_SERVICE}:
        raise AssetConflict(f"asset in {state.value} state cannot be assigned")
    if request.expected_return_on and request.expected_return_on < request.starts_on:
        raise ValueError("expected return date precedes assignment start")
    if _active_assignment(db, tenant_id, asset.id) is not None:
        raise AssetConflict("asset already has an active assignment")

    previous_location = asset.location_id
    row = AssetAssignment(
        tenant_id=tenant_id,
        asset_id=asset.id,
        custodian_id=request.custodian_id,
        starts_on=request.starts_on,
        expected_return_on=request.expected_return_on,
        status=AssignmentStatus.ACTIVE.value,
        condition_on_issue=request.condition_on_issue.value
        if request.condition_on_issue
        else None,
        location_id=(
            request.location_id
            if request.location_id is not None
            else asset.location_id
        ),
        notes=_optional_clean(request.notes),
        created_by_id=request.actor_id,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            if state is AssetState.REGISTERED:
                asset.state = transition_asset(
                    state,
                    AssetState.IN_SERVICE,
                    expected=AssetState.REGISTERED,
                ).value
            if request.location_id is not None:
                asset.location_id = request.location_id
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                asset_id=asset.id,
                event_type="assignment_started",
                actor_id=request.actor_id,
                source_id=row.id,
                new_custodian_id=row.custodian_id,
                notes=row.notes,
            )
            if state is AssetState.REGISTERED:
                _event(
                    db,
                    tenant_id=tenant_id,
                    asset_id=asset.id,
                    event_type="asset_state_changed",
                    actor_id=request.actor_id,
                    source_id=row.id,
                    previous_state=state.value,
                    new_state=asset.state,
                    notes="asset entered service on assignment",
                )
            if previous_location != asset.location_id:
                _event(
                    db,
                    tenant_id=tenant_id,
                    asset_id=asset.id,
                    event_type="asset_location_changed",
                    actor_id=request.actor_id,
                    source_id=row.id,
                    previous_location_id=previous_location,
                    new_location_id=asset.location_id,
                    notes="asset location changed on assignment",
                )
            db.flush()
    except IntegrityError as exc:
        raise AssetConflict("asset already has an active assignment") from exc
    return row


def end_assignment(
    db: Session, *, tenant_id: UUID, request: AssignmentEnd
) -> AssetAssignment:
    asset, row = _assignment_scope(db, tenant_id, request.assignment_id)
    if request.requested not in {
        AssignmentStatus.RETURNED,
        AssignmentStatus.LOST,
    }:
        raise ValueError("end assignment only accepts returned or lost")
    if request.ended_on < row.starts_on:
        raise ValueError("assignment end precedes assignment start")
    current = AssignmentStatus(row.status)
    row.status = transition_assignment(
        current, request.requested, expected=request.expected
    ).value
    row.ended_on = request.ended_on
    row.condition_on_return = (
        request.condition_on_return.value if request.condition_on_return else None
    )
    row.ended_by_id = request.actor_id
    if request.notes is not None:
        row.notes = _optional_clean(request.notes)
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type=(
            "assignment_returned"
            if request.requested is AssignmentStatus.RETURNED
            else "assignment_lost"
        ),
        actor_id=request.actor_id,
        source_id=row.id,
        previous_custodian_id=row.custodian_id,
        notes=row.notes,
    )
    db.flush()
    return row


def transfer_asset(
    db: Session, *, tenant_id: UUID, request: AssignmentTransfer
) -> AssetAssignment:
    asset, previous = _assignment_scope(db, tenant_id, request.assignment_id)
    if request.transferred_on < previous.starts_on:
        raise ValueError("transfer date precedes assignment start")
    if (
        request.expected_return_on
        and request.expected_return_on < request.transferred_on
    ):
        raise ValueError("expected return date precedes transfer")
    current = AssignmentStatus(previous.status)
    transition_assignment(
        current, AssignmentStatus.TRANSFERRED, expected=request.expected
    )
    previous_location = asset.location_id
    row = AssetAssignment(
        tenant_id=tenant_id,
        asset_id=asset.id,
        custodian_id=request.new_custodian_id,
        preceding_assignment_id=previous.id,
        starts_on=request.transferred_on,
        expected_return_on=request.expected_return_on,
        status=AssignmentStatus.ACTIVE.value,
        condition_on_issue=request.condition_on_issue.value
        if request.condition_on_issue
        else None,
        location_id=(
            request.new_location_id
            if request.new_location_id is not None
            else asset.location_id
        ),
        notes=_optional_clean(request.notes),
        created_by_id=request.actor_id,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            previous.status = AssignmentStatus.TRANSFERRED.value
            previous.ended_on = request.transferred_on
            previous.ended_by_id = request.actor_id
            if request.new_location_id is not None:
                asset.location_id = request.new_location_id
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                asset_id=asset.id,
                event_type="assignment_transferred",
                actor_id=request.actor_id,
                source_id=row.id,
                previous_custodian_id=previous.custodian_id,
                new_custodian_id=row.custodian_id,
                notes=row.notes,
            )
            if previous_location != asset.location_id:
                _event(
                    db,
                    tenant_id=tenant_id,
                    asset_id=asset.id,
                    event_type="asset_location_changed",
                    actor_id=request.actor_id,
                    source_id=row.id,
                    previous_location_id=previous_location,
                    new_location_id=asset.location_id,
                    notes="asset location changed on transfer",
                )
            db.flush()
    except IntegrityError as exc:
        raise AssetConflict("asset already has an active assignment") from exc
    return row


def schedule_maintenance(
    db: Session, *, tenant_id: UUID, request: MaintenanceSchedule
) -> AssetMaintenance:
    asset = _asset(db, tenant_id, request.asset_id)
    state = AssetState(asset.state)
    if state in {AssetState.RETIRED, AssetState.DISPOSED}:
        raise AssetConflict(f"asset in {state.value} state cannot be maintained")
    row = AssetMaintenance(
        tenant_id=tenant_id,
        asset_id=asset.id,
        kind=request.kind.value,
        summary=_clean(request.summary, "maintenance summary"),
        description=_optional_clean(request.description),
        status=MaintenanceStatus.SCHEDULED.value,
        scheduled_for=request.scheduled_for,
        provider_ref=_optional_clean(request.provider_ref),
        created_by_id=request.actor_id,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="maintenance_scheduled",
        actor_id=request.actor_id,
        source_id=row.id,
        notes=row.summary,
    )
    db.flush()
    return row


def start_maintenance(
    db: Session,
    *,
    tenant_id: UUID,
    maintenance_id: UUID,
    expected: MaintenanceStatus,
    actor_id: UUID | None,
    started_at: datetime | None = None,
) -> AssetMaintenance:
    asset, row = _maintenance_scope(db, tenant_id, maintenance_id)
    asset_state = AssetState(asset.state)
    if asset_state not in {AssetState.IN_SERVICE, AssetState.OUT_OF_SERVICE}:
        raise AssetConflict(
            f"asset in {asset_state.value} state cannot start maintenance"
        )
    if (
        _in_progress_maintenance(
            db,
            tenant_id,
            asset.id,
            excluding_id=row.id,
        )
        is not None
    ):
        raise AssetConflict("asset maintenance already in progress")
    row.status = transition_maintenance(
        MaintenanceStatus(row.status),
        MaintenanceStatus.IN_PROGRESS,
        expected=expected,
    ).value
    row.started_at = started_at or datetime.now(UTC)
    row.asset_state_before = asset_state.value
    if asset_state is AssetState.IN_SERVICE:
        asset.state = transition_asset(
            asset_state,
            AssetState.OUT_OF_SERVICE,
            expected=AssetState.IN_SERVICE,
        ).value
        _event(
            db,
            tenant_id=tenant_id,
            asset_id=asset.id,
            event_type="asset_state_changed",
            actor_id=actor_id,
            source_id=row.id,
            occurred_at=row.started_at,
            previous_state=asset_state.value,
            new_state=asset.state,
            notes="asset taken out of service for maintenance",
        )
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="maintenance_started",
        actor_id=actor_id,
        source_id=row.id,
        occurred_at=row.started_at,
        notes=row.summary,
    )
    db.flush()
    return row


def complete_maintenance(
    db: Session, *, tenant_id: UUID, request: MaintenanceComplete
) -> AssetMaintenance:
    asset, row = _maintenance_scope(db, tenant_id, request.maintenance_id)
    if row.started_at is not None and request.completed_on < row.started_at.date():
        raise ValueError("maintenance completion precedes its start")
    row.status = transition_maintenance(
        MaintenanceStatus(row.status),
        MaintenanceStatus.COMPLETED,
        expected=request.expected,
    ).value
    row.completed_on = request.completed_on
    row.work_performed = _clean(request.work_performed, "work performed")
    row.next_due_on = request.next_due_on
    row.notes = _optional_clean(request.notes)
    row.completed_by_id = request.actor_id
    if (
        request.return_to_service
        and row.asset_state_before == AssetState.IN_SERVICE.value
        and AssetState(asset.state) is AssetState.OUT_OF_SERVICE
    ):
        previous = AssetState(asset.state)
        asset.state = transition_asset(
            previous,
            AssetState.IN_SERVICE,
            expected=AssetState.OUT_OF_SERVICE,
        ).value
        _event(
            db,
            tenant_id=tenant_id,
            asset_id=asset.id,
            event_type="asset_state_changed",
            actor_id=request.actor_id,
            source_id=row.id,
            previous_state=previous.value,
            new_state=asset.state,
            notes="asset returned to service after maintenance",
        )
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="maintenance_completed",
        actor_id=request.actor_id,
        source_id=row.id,
        notes=row.work_performed,
    )
    db.flush()
    return row


def cancel_maintenance(
    db: Session, *, tenant_id: UUID, request: MaintenanceCancel
) -> AssetMaintenance:
    asset, row = _maintenance_scope(db, tenant_id, request.maintenance_id)
    row.status = transition_maintenance(
        MaintenanceStatus(row.status),
        MaintenanceStatus.CANCELLED,
        expected=request.expected,
    ).value
    if (
        row.asset_state_before == AssetState.IN_SERVICE.value
        and AssetState(asset.state) is AssetState.OUT_OF_SERVICE
    ):
        asset.state = transition_asset(
            AssetState(asset.state),
            AssetState.IN_SERVICE,
            expected=AssetState.OUT_OF_SERVICE,
        ).value
        _event(
            db,
            tenant_id=tenant_id,
            asset_id=asset.id,
            event_type="asset_state_changed",
            actor_id=request.actor_id,
            source_id=row.id,
            previous_state=AssetState.OUT_OF_SERVICE.value,
            new_state=asset.state,
            notes="asset returned to service after maintenance cancellation",
        )
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="maintenance_cancelled",
        actor_id=request.actor_id,
        source_id=row.id,
        notes=_clean(request.reason, "cancellation reason"),
    )
    db.flush()
    return row


def request_disposal(
    db: Session, *, tenant_id: UUID, request: DisposalRequest
) -> AssetDisposal:
    asset = _asset(db, tenant_id, request.asset_id)
    if AssetState(asset.state) is not AssetState.RETIRED:
        raise AssetConflict("asset must be retired before disposal is requested")
    _retirement_ready(db, tenant_id, asset.id)
    row = AssetDisposal(
        tenant_id=tenant_id,
        asset_id=asset.id,
        method=request.method.value,
        status=DisposalStatus.REQUESTED.value,
        requested_on=request.requested_on,
        requested_by_id=request.actor_id,
        reason=_clean(request.reason, "disposal reason"),
        recipient_ref=_optional_clean(request.recipient_ref),
        external_authorization_ref=_optional_clean(request.external_authorization_ref),
        notes=_optional_clean(request.notes),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                asset_id=asset.id,
                event_type="disposal_requested",
                actor_id=request.actor_id,
                source_id=row.id,
                notes=row.reason,
            )
            db.flush()
    except IntegrityError as exc:
        raise AssetConflict("asset already has an open disposal") from exc
    return row


def approve_disposal(
    db: Session, *, tenant_id: UUID, request: DisposalApprove
) -> AssetDisposal:
    asset, row = _disposal_scope(db, tenant_id, request.disposal_id)
    if request.approved_at.date() < row.requested_on:
        raise ValueError("disposal approval precedes its request")
    row.status = transition_disposal(
        DisposalStatus(row.status),
        DisposalStatus.APPROVED,
        expected=request.expected,
        requested_by_id=row.requested_by_id,
        actor_id=request.actor_id,
    ).value
    row.approved_by_id = request.actor_id
    row.approved_at = request.approved_at
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="disposal_approved",
        actor_id=request.actor_id,
        source_id=row.id,
    )
    db.flush()
    return row


def complete_disposal(
    db: Session, *, tenant_id: UUID, request: DisposalComplete
) -> AssetDisposal:
    asset, row = _disposal_scope(db, tenant_id, request.disposal_id)
    if request.disposed_on < row.requested_on:
        raise ValueError("disposal completion precedes its request")
    if AssetState(asset.state) is not AssetState.RETIRED:
        raise AssetConflict("only a retired asset can complete disposal")
    _retirement_ready(db, tenant_id, asset.id)
    row.status = transition_disposal(
        DisposalStatus(row.status),
        DisposalStatus.COMPLETED,
        expected=request.expected,
        requested_by_id=row.requested_by_id,
        actor_id=request.actor_id,
    ).value
    row.disposed_on = request.disposed_on
    row.completed_by_id = request.actor_id
    row.external_finance_ref = _optional_clean(request.external_finance_ref)
    if request.notes is not None:
        row.notes = _optional_clean(request.notes)
    previous = AssetState(asset.state)
    asset.state = transition_asset(
        previous, AssetState.DISPOSED, expected=AssetState.RETIRED
    ).value
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="disposal_completed",
        actor_id=request.actor_id,
        source_id=row.id,
        notes=row.notes,
    )
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="asset_state_changed",
        actor_id=request.actor_id,
        source_id=row.id,
        previous_state=previous.value,
        new_state=asset.state,
        notes="asset disposal completed",
    )
    db.flush()
    return row


def cancel_disposal(
    db: Session, *, tenant_id: UUID, request: DisposalCancel
) -> AssetDisposal:
    asset, row = _disposal_scope(db, tenant_id, request.disposal_id)
    row.status = transition_disposal(
        DisposalStatus(row.status),
        DisposalStatus.CANCELLED,
        expected=request.expected,
        requested_by_id=row.requested_by_id,
        actor_id=request.actor_id,
    ).value
    _event(
        db,
        tenant_id=tenant_id,
        asset_id=asset.id,
        event_type="disposal_cancelled",
        actor_id=request.actor_id,
        source_id=row.id,
        notes=_clean(request.reason, "cancellation reason"),
    )
    db.flush()
    return row


__all__ = [
    "AssetConflict",
    "AssetNotFound",
    "approve_disposal",
    "assign_asset",
    "cancel_disposal",
    "cancel_maintenance",
    "complete_disposal",
    "complete_maintenance",
    "create_asset",
    "create_asset_snapshot",
    "end_assignment",
    "move_asset",
    "request_disposal",
    "schedule_maintenance",
    "start_maintenance",
    "transfer_asset",
    "transition_asset_state",
]
