"""Flush-only provider-neutral OLT/ONT/PON lifecycle owner."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.db import conflict_savepoint
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_pon_access.contracts import (
    AdmitOnt,
    AssignOnt,
    BackupEvidence,
    CommissioningResult,
    CommissionOnt,
    DesiredConfigState,
    DesiredServiceQuery,
    DesiredServiceSnapshot,
    OltLookup,
    OltSnapshot,
    OltState,
    OntLookup,
    OntSnapshot,
    OntState,
    PonDriftReport,
    PonPortLookup,
    PonPortSnapshot,
    ReconcilePon,
    RecordBackupEvidence,
    RecordPonObservation,
    RegisterOlt,
    RegisterPonPort,
    SetDesiredService,
)
from dotmac_pon_access.models import (
    BackupEvidenceRow,
    DesiredService,
    Olt,
    Ont,
    PonEvent,
    PonObservation,
    PonPort,
    PonReconciliation,
)


class PonAccessError(ValueError):
    """Base PON contract violation."""


class PonAccessNotFound(PonAccessError):
    """A tenant-owned PON aggregate was not found."""


class PonAccessConflict(PonAccessError):
    """The expected state or idempotency contract changed."""


def _clean(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise PonAccessError(f"{label} must not be blank")
    return result


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    aggregate_ref: str,
    event_type: str,
    evidence_ref: str,
    payload: dict[str, str],
    occurred_at: datetime,
) -> None:
    db.add(
        PonEvent(
            tenant_id=tenant_id,
            aggregate_ref=aggregate_ref,
            event_type=event_type,
            evidence_ref=evidence_ref,
            payload=payload,
            occurred_at=occurred_at,
        )
    )


def _olt_snapshot(row: Olt) -> OltSnapshot:
    return OltSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        name=row.name,
        management_ref=row.management_ref,
        vendor_family=row.vendor_family,
        capability_codes=tuple(row.capability_codes),
        state=OltState(row.state),
        node_ref=row.node_ref,
        asset_ref=row.asset_ref,
        created_at=row.created_at,
    )


def _port_snapshot(row: PonPort) -> PonPortSnapshot:
    return PonPortSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        olt_id=row.olt_id,
        slot=row.slot,
        port=row.port,
        label=row.label,
        capacity=row.capacity,
        fiber_endpoint_ref=row.fiber_endpoint_ref,
        created_at=row.created_at,
    )


def _ont_snapshot(row: Ont) -> OntSnapshot:
    return OntSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        serial_number=row.serial_number,
        vendor_family=row.vendor_family,
        pon_port_id=row.pon_port_id,
        state=OntState(row.state),
        service_subject_ref=row.service_subject_ref,
        assignment_ref=row.assignment_ref,
        registration_ref=row.registration_ref,
        asset_ref=row.asset_ref,
        admitted_at=row.admitted_at,
        commissioned_at=row.commissioned_at,
    )


def _desired_snapshot(row: DesiredService) -> DesiredServiceSnapshot:
    return DesiredServiceSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        ont_id=row.ont_id,
        service_ref=row.service_ref,
        profile_code=row.profile_code,
        vlan_ref=row.vlan_ref,
        ip_assignment_ref=row.ip_assignment_ref,
        desired_fingerprint=row.desired_fingerprint,
        observed_fingerprint=row.observed_fingerprint,
        state=DesiredConfigState(row.state),
        decision_ref=row.decision_ref,
        updated_at=row.updated_at,
    )


def _backup_snapshot(row: BackupEvidenceRow) -> BackupEvidence:
    return BackupEvidence(
        id=row.id,
        tenant_id=row.tenant_id,
        olt_id=row.olt_id,
        backup_ref=row.backup_ref,
        configuration_fingerprint=row.configuration_fingerprint,
        captured_at=row.captured_at,
        source_ref=row.source_ref,
    )


def register_olt(db: Session, *, tenant_id: UUID, command: RegisterOlt) -> OltSnapshot:
    capabilities = tuple(
        dict.fromkeys(
            _clean(code, "capability code") for code in command.capability_codes
        )
    )
    if not capabilities:
        raise PonAccessError(
            "OLT must declare at least one provider-neutral capability"
        )
    now = datetime.now(UTC)
    row = Olt(
        tenant_id=tenant_id,
        code=_clean(command.code, "OLT code"),
        name=_clean(command.name, "OLT name"),
        management_ref=_clean(command.management_ref, "management reference"),
        vendor_family=_clean(command.vendor_family, "vendor family"),
        capability_codes=list(capabilities),
        state=OltState.ACTIVE.value,
        node_ref=command.node_ref,
        asset_ref=command.asset_ref,
        created_at=now,
    )
    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"olt:{row.id}",
                event_type="olt_registered",
                evidence_ref=row.management_ref,
                payload={"code": row.code, "vendor_family": row.vendor_family},
                occurred_at=now,
            )
            db.flush()
    except IntegrityError as exc:
        raise PonAccessConflict("OLT code already exists") from exc
    return _olt_snapshot(row)


def register_pon_port(
    db: Session, *, tenant_id: UUID, command: RegisterPonPort
) -> PonPortSnapshot:
    if command.slot < 0 or command.port < 0 or command.capacity <= 0:
        raise PonAccessError(
            "PON port position must be non-negative and capacity positive"
        )
    olt = db.scalar(
        select(Olt)
        .where(Olt.tenant_id == tenant_id, Olt.id == command.olt_id)
        .with_for_update()
    )
    if olt is None:
        raise PonAccessNotFound("OLT not found")
    if OltState(olt.state) is OltState.RETIRED:
        raise PonAccessConflict("cannot add a port to a retired OLT")
    row = PonPort(
        tenant_id=tenant_id,
        olt_id=olt.id,
        slot=command.slot,
        port=command.port,
        label=_clean(command.label, "PON port label"),
        capacity=command.capacity,
        fiber_endpoint_ref=command.fiber_endpoint_ref,
        created_at=datetime.now(UTC),
    )
    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise PonAccessConflict("PON port position already exists") from exc
    return _port_snapshot(row)


def admit_ont(db: Session, *, tenant_id: UUID, command: AdmitOnt) -> OntSnapshot:
    port = db.scalar(
        select(PonPort)
        .where(PonPort.tenant_id == tenant_id, PonPort.id == command.pon_port_id)
        .with_for_update()
    )
    if port is None:
        raise PonAccessNotFound("PON port not found")
    olt = db.scalar(
        select(Olt).where(Olt.tenant_id == tenant_id, Olt.id == port.olt_id)
    )
    if olt is None or OltState(olt.state) is not OltState.ACTIVE:
        raise PonAccessConflict("ONT admission requires an active OLT")
    active_count = db.scalar(
        select(func.count())
        .select_from(Ont)
        .where(
            Ont.tenant_id == tenant_id,
            Ont.pon_port_id == port.id,
            Ont.state != OntState.RETIRED.value,
        )
    )
    if int(active_count or 0) >= port.capacity:
        raise PonAccessConflict("PON port capacity is exhausted")
    row = Ont(
        tenant_id=tenant_id,
        serial_number=_clean(command.serial_number, "ONT serial number").upper(),
        vendor_family=_clean(command.vendor_family, "vendor family"),
        pon_port_id=port.id,
        state=OntState.ADMITTED.value,
        registration_ref=_clean(command.registration_ref, "registration reference"),
        asset_ref=command.asset_ref,
        admitted_at=command.observed_at,
    )
    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"ont:{row.id}",
                event_type="ont_admitted",
                evidence_ref=row.registration_ref,
                payload={"pon_port_id": str(row.pon_port_id)},
                occurred_at=command.observed_at,
            )
            db.flush()
    except IntegrityError as exc:
        raise PonAccessConflict(
            "ONT serial or registration reference already exists"
        ) from exc
    return _ont_snapshot(row)


def assign_ont(db: Session, *, tenant_id: UUID, command: AssignOnt) -> OntSnapshot:
    row = db.scalar(
        select(Ont)
        .where(Ont.tenant_id == tenant_id, Ont.id == command.ont_id)
        .with_for_update()
    )
    if row is None:
        raise PonAccessNotFound("ONT not found")
    if OntState(row.state) is not command.expected:
        raise PonAccessConflict("ONT assignment state changed")
    if command.expected is not OntState.ADMITTED:
        raise PonAccessConflict("only an admitted ONT can be assigned")
    row.service_subject_ref = _clean(
        command.service_subject_ref, "service subject reference"
    )
    row.assignment_ref = _clean(command.assignment_ref, "assignment reference")
    row.state = OntState.ASSIGNED.value
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"ont:{row.id}",
        event_type="ont_assigned",
        evidence_ref=row.assignment_ref,
        payload={"service_subject_ref": row.service_subject_ref},
        occurred_at=command.assigned_at,
    )
    db.flush()
    return _ont_snapshot(row)


def commission_ont(
    db: Session, *, tenant_id: UUID, command: CommissionOnt
) -> CommissioningResult:
    row = db.scalar(
        select(Ont)
        .where(Ont.tenant_id == tenant_id, Ont.id == command.ont_id)
        .with_for_update()
    )
    if row is None:
        raise PonAccessNotFound("ONT not found")
    if (
        OntState(row.state) is OntState.COMMISSIONED
        and row.operation_ref == command.operation_ref
        and row.commissioned_profile_code == command.profile_code
        and row.desired_config_ref == command.desired_config_ref
    ):
        return CommissioningResult(
            ont=_ont_snapshot(row),
            profile_code=row.commissioned_profile_code,
            desired_config_ref=row.desired_config_ref,
            operation_ref=row.operation_ref,
            changed=False,
        )
    if OntState(row.state) is not command.expected:
        raise PonAccessConflict("ONT commissioning state changed")
    if command.expected is not OntState.ASSIGNED:
        raise PonAccessConflict("only an assigned ONT can be commissioned")
    row.commissioned_profile_code = _clean(command.profile_code, "profile code")
    row.desired_config_ref = _clean(
        command.desired_config_ref, "desired configuration reference"
    )
    row.operation_ref = _clean(command.operation_ref, "operation reference")
    row.commissioned_at = command.commissioned_at
    row.state = OntState.COMMISSIONED.value
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"ont:{row.id}",
        event_type="ont_commissioned",
        evidence_ref=row.operation_ref,
        payload={
            "profile_code": row.commissioned_profile_code,
            "desired_config_ref": row.desired_config_ref,
        },
        occurred_at=command.commissioned_at,
    )
    db.flush()
    return CommissioningResult(
        ont=_ont_snapshot(row),
        profile_code=row.commissioned_profile_code,
        desired_config_ref=row.desired_config_ref,
        operation_ref=row.operation_ref,
        changed=True,
    )


def set_desired_service(
    db: Session, *, tenant_id: UUID, command: SetDesiredService
) -> DesiredServiceSnapshot:
    ont = db.scalar(
        select(Ont)
        .where(Ont.tenant_id == tenant_id, Ont.id == command.ont_id)
        .with_for_update()
    )
    if ont is None:
        raise PonAccessNotFound("ONT not found")
    if OntState(ont.state) in {OntState.DISCOVERED, OntState.RETIRED}:
        raise PonAccessConflict("ONT cannot accept desired service state")
    service_ref = _clean(command.service_ref, "service reference")
    row = db.scalar(
        select(DesiredService)
        .where(
            DesiredService.tenant_id == tenant_id,
            DesiredService.ont_id == ont.id,
            DesiredService.service_ref == service_ref,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None:
        row = DesiredService(
            tenant_id=tenant_id,
            ont_id=ont.id,
            service_ref=service_ref,
            profile_code=_clean(command.profile_code, "profile code"),
            vlan_ref=command.vlan_ref,
            ip_assignment_ref=command.ip_assignment_ref,
            desired_fingerprint=_clean(
                command.desired_fingerprint, "desired fingerprint"
            ),
            state=DesiredConfigState.PENDING.value,
            decision_ref=_clean(command.decision_ref, "decision reference"),
            updated_at=now,
        )
        db.add(row)
    else:
        row.profile_code = _clean(command.profile_code, "profile code")
        row.vlan_ref = command.vlan_ref
        row.ip_assignment_ref = command.ip_assignment_ref
        row.desired_fingerprint = _clean(
            command.desired_fingerprint, "desired fingerprint"
        )
        row.state = DesiredConfigState.PENDING.value
        row.decision_ref = _clean(command.decision_ref, "decision reference")
        row.updated_at = now
    db.flush()
    return _desired_snapshot(row)


def record_pon_observation(
    db: Session, *, tenant_id: UUID, command: RecordPonObservation
) -> UUID:
    source_ref = _clean(command.source_ref, "source reference")
    fingerprint = _clean(command.fingerprint, "fingerprint")
    existing = db.scalar(
        select(PonObservation).where(
            PonObservation.tenant_id == tenant_id,
            PonObservation.source_ref == source_ref,
            PonObservation.fingerprint == fingerprint,
        )
    )
    if existing is not None:
        if (
            existing.subject_ref != command.subject_ref
            or existing.observation_kind != command.observation_kind
            or existing.value != command.value
        ):
            raise PonAccessConflict(
                "observation fingerprint reused with different facts"
            )
        return existing.id
    row = PonObservation(
        tenant_id=tenant_id,
        subject_ref=_clean(command.subject_ref, "observation subject"),
        observation_kind=_clean(command.observation_kind, "observation kind"),
        value=_clean(command.value, "observation value"),
        unit=command.unit,
        source_ref=source_ref,
        observed_at=command.observed_at,
        fingerprint=fingerprint,
    )
    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise PonAccessConflict("observation fingerprint already exists") from exc
    return row.id


def reconcile_pon(
    db: Session, *, tenant_id: UUID, command: ReconcilePon
) -> PonDriftReport:
    row = db.scalar(
        select(DesiredService)
        .where(
            DesiredService.tenant_id == tenant_id,
            DesiredService.ont_id == command.ont_id,
            DesiredService.service_ref == command.service_ref,
        )
        .with_for_update()
    )
    if row is None:
        raise PonAccessNotFound("desired PON service not found")
    observed = _clean(command.observed_fingerprint, "observed fingerprint")
    drifted = row.desired_fingerprint != observed
    row.observed_fingerprint = observed
    row.state = (
        DesiredConfigState.DRIFTED.value
        if drifted
        else DesiredConfigState.APPLIED.value
    )
    row.updated_at = command.observed_at
    reason = "configuration-fingerprint-mismatch" if drifted else None
    db.add(
        PonReconciliation(
            tenant_id=tenant_id,
            desired_service_id=row.id,
            observed_fingerprint=observed,
            evidence_ref=_clean(command.evidence_ref, "reconciliation evidence"),
            drifted=drifted,
            reason_code=reason,
            reconciled_at=command.observed_at,
        )
    )
    if drifted:
        _event(
            db,
            tenant_id=tenant_id,
            aggregate_ref=f"ont:{row.ont_id}",
            event_type="pon_drift_detected",
            evidence_ref=command.evidence_ref,
            payload={"service_ref": row.service_ref},
            occurred_at=command.observed_at,
        )
    db.flush()
    return PonDriftReport(
        desired=_desired_snapshot(row),
        drifted=drifted,
        evidence_ref=command.evidence_ref,
        reason_code=reason,
        reconciled_at=command.observed_at,
    )


def record_backup_evidence(
    db: Session, *, tenant_id: UUID, command: RecordBackupEvidence
) -> BackupEvidence:
    olt = db.scalar(
        select(Olt).where(Olt.tenant_id == tenant_id, Olt.id == command.olt_id)
    )
    if olt is None:
        raise PonAccessNotFound("OLT not found")
    backup_ref = _clean(command.backup_ref, "backup reference")
    existing = db.scalar(
        select(BackupEvidenceRow).where(
            BackupEvidenceRow.tenant_id == tenant_id,
            BackupEvidenceRow.backup_ref == backup_ref,
        )
    )
    if existing is not None:
        if (
            existing.olt_id != command.olt_id
            or existing.configuration_fingerprint != command.configuration_fingerprint
        ):
            raise PonAccessConflict("backup reference reused with different evidence")
        return _backup_snapshot(existing)
    row = BackupEvidenceRow(
        tenant_id=tenant_id,
        olt_id=olt.id,
        backup_ref=backup_ref,
        configuration_fingerprint=_clean(
            command.configuration_fingerprint, "configuration fingerprint"
        ),
        captured_at=command.captured_at,
        source_ref=_clean(command.source_ref, "source reference"),
    )
    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise PonAccessConflict("backup reference already exists") from exc
    return _backup_snapshot(row)


def lookup_olts(
    db: Session, *, tenant_id: UUID, query: OltLookup
) -> tuple[OltSnapshot, ...]:
    statement = select(Olt).where(Olt.tenant_id == tenant_id)
    if query.olt_id is not None:
        statement = statement.where(Olt.id == query.olt_id)
    if query.code is not None:
        statement = statement.where(Olt.code == query.code)
    return tuple(_olt_snapshot(row) for row in db.scalars(statement))


def lookup_pon_ports(
    db: Session, *, tenant_id: UUID, query: PonPortLookup
) -> tuple[PonPortSnapshot, ...]:
    statement = select(PonPort).where(PonPort.tenant_id == tenant_id)
    if query.pon_port_id is not None:
        statement = statement.where(PonPort.id == query.pon_port_id)
    if query.olt_id is not None:
        statement = statement.where(PonPort.olt_id == query.olt_id)
    return tuple(_port_snapshot(row) for row in db.scalars(statement))


def lookup_onts(
    db: Session, *, tenant_id: UUID, query: OntLookup
) -> tuple[OntSnapshot, ...]:
    statement = select(Ont).where(Ont.tenant_id == tenant_id)
    if query.ont_id is not None:
        statement = statement.where(Ont.id == query.ont_id)
    if query.serial_number is not None:
        statement = statement.where(Ont.serial_number == query.serial_number.upper())
    if query.service_subject_ref is not None:
        statement = statement.where(
            Ont.service_subject_ref == query.service_subject_ref
        )
    return tuple(_ont_snapshot(row) for row in db.scalars(statement))


def query_desired_services(
    db: Session, *, tenant_id: UUID, query: DesiredServiceQuery
) -> tuple[DesiredServiceSnapshot, ...]:
    statement = select(DesiredService).where(
        DesiredService.tenant_id == tenant_id,
        DesiredService.ont_id == query.ont_id,
    )
    if query.service_ref is not None:
        statement = statement.where(DesiredService.service_ref == query.service_ref)
    return tuple(_desired_snapshot(row) for row in db.scalars(statement))


__all__ = [
    "PonAccessConflict",
    "PonAccessError",
    "PonAccessNotFound",
    "admit_ont",
    "assign_ont",
    "commission_ont",
    "lookup_olts",
    "lookup_onts",
    "lookup_pon_ports",
    "query_desired_services",
    "reconcile_pon",
    "record_backup_evidence",
    "record_pon_observation",
    "register_olt",
    "register_pon_port",
    "set_desired_service",
]
