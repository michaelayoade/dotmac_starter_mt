"""Flush-only network assurance owner; ticket/work-order decisions stay outside."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_network_assurance.contracts import (
    ClassifyImpact,
    EscalationRecommendation,
    ImpactQuery,
    ImpactSeverity,
    ImpactSnapshot,
    IncidentLookup,
    IncidentSnapshot,
    IncidentState,
    MaintenanceQuery,
    MaintenanceSnapshot,
    MaintenanceState,
    OpenIncident,
    RecordNotificationEvidence,
    RecordSlaEvidence,
    ResolveIncident,
    ScheduleMaintenance,
    SlaEvidenceQuery,
    SlaEvidenceSnapshot,
    UpdateIncident,
)
from dotmac_network_assurance.models import (
    Impact,
    Incident,
    IncidentEvent,
    MaintenanceWindow,
    NotificationEvidence,
    SlaEvidence,
)


class AssuranceError(ValueError):
    pass


class AssuranceNotFound(AssuranceError):
    pass


class AssuranceConflict(AssuranceError):
    pass


def _clean(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise AssuranceError(f"{label} must not be blank")
    return result


def _incident(
    db: Session, tenant_id: UUID, incident_id: UUID, *, lock: bool = False
) -> Incident:
    statement = select(Incident).where(
        Incident.tenant_id == tenant_id, Incident.id == incident_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise AssuranceNotFound("incident not found")
    return row


def _incident_snapshot(row: Incident) -> IncidentSnapshot:
    return IncidentSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        summary=row.summary,
        severity=ImpactSeverity(row.severity),
        state=IncidentState(row.state),
        detection_ref=row.detection_ref,
        source_observation_refs=tuple(row.source_observation_refs),
        detected_at=row.detected_at,
        resolved_at=row.resolved_at,
        resolution_code=row.resolution_code,
        resolution_summary=row.resolution_summary,
    )


def _impact_snapshot(row: Impact) -> ImpactSnapshot:
    return ImpactSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        subject_ref=row.subject_ref,
        subject_kind=row.subject_kind,
        severity=ImpactSeverity(row.severity),
        topology_path_ref=row.topology_path_ref,
        reason_code=row.reason_code,
        evaluated_at=row.evaluated_at,
    )


def _event(
    db: Session,
    incident: Incident,
    kind: str,
    evidence_ref: str,
    payload: dict[str, str],
    occurred_at: datetime | None = None,
) -> None:
    db.add(
        IncidentEvent(
            tenant_id=incident.tenant_id,
            incident_id=incident.id,
            event_type=kind,
            evidence_ref=evidence_ref,
            payload=payload,
            occurred_at=occurred_at or datetime.now(UTC),
        )
    )


def open_incident(
    db: Session, *, tenant_id: UUID, command: OpenIncident
) -> IncidentSnapshot:
    row = Incident(
        tenant_id=tenant_id,
        code=_clean(command.code, "incident code"),
        summary=_clean(command.summary, "incident summary"),
        severity=command.severity.value,
        state=IncidentState.OPEN.value,
        detection_ref=_clean(command.detection_ref, "detection reference"),
        source_observation_refs=list(command.source_observation_refs),
        detected_at=command.detected_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                row,
                "incident_opened",
                row.detection_ref,
                {"severity": row.severity},
                row.detected_at,
            )
            db.flush()
    except IntegrityError as exc:
        raise AssuranceConflict("incident code already exists") from exc
    return _incident_snapshot(row)


def classify_impact(
    db: Session, *, tenant_id: UUID, command: ClassifyImpact
) -> ImpactSnapshot:
    incident = _incident(db, tenant_id, command.incident_id)
    row = db.scalar(
        select(Impact)
        .where(
            Impact.tenant_id == tenant_id,
            Impact.incident_id == incident.id,
            Impact.subject_ref == command.subject_ref,
        )
        .with_for_update()
    )
    if row is None:
        row = Impact(
            tenant_id=tenant_id,
            incident_id=incident.id,
            subject_ref=_clean(command.subject_ref, "subject reference"),
            subject_kind=_clean(command.subject_kind, "subject kind"),
            severity=command.severity.value,
            topology_path_ref=command.topology_path_ref,
            reason_code=_clean(command.reason_code, "reason code"),
            evaluated_at=command.evaluated_at,
        )
        db.add(row)
    else:
        row.subject_kind = _clean(command.subject_kind, "subject kind")
        row.severity = command.severity.value
        row.topology_path_ref = command.topology_path_ref
        row.reason_code = _clean(command.reason_code, "reason code")
        row.evaluated_at = command.evaluated_at
    _event(
        db,
        incident,
        "impact_classified",
        command.reason_code,
        {"subject_ref": command.subject_ref},
        command.evaluated_at,
    )
    db.flush()
    return _impact_snapshot(row)


_TRANSITIONS = {
    IncidentState.OPEN: {IncidentState.INVESTIGATING, IncidentState.RESOLVED},
    IncidentState.INVESTIGATING: {IncidentState.MONITORING, IncidentState.RESOLVED},
    IncidentState.MONITORING: {IncidentState.INVESTIGATING, IncidentState.RESOLVED},
    IncidentState.RESOLVED: set(),
}


def update_incident(
    db: Session, *, tenant_id: UUID, command: UpdateIncident
) -> IncidentSnapshot:
    row = _incident(db, tenant_id, command.incident_id, lock=True)
    current = IncidentState(row.state)
    if (
        current is not command.expected
        or command.requested not in _TRANSITIONS[current]
    ):
        raise AssuranceConflict("incident transition refused")
    row.state = command.requested.value
    _event(
        db,
        row,
        "incident_changed",
        _clean(command.evidence_ref, "evidence reference"),
        {
            "previous": current.value,
            "requested": command.requested.value,
            "note": command.note or "",
        },
    )
    db.flush()
    return _incident_snapshot(row)


def resolve_incident(
    db: Session, *, tenant_id: UUID, command: ResolveIncident
) -> IncidentSnapshot:
    row = _incident(db, tenant_id, command.incident_id, lock=True)
    current = IncidentState(row.state)
    if (
        current is not command.expected
        or IncidentState.RESOLVED not in _TRANSITIONS[current]
    ):
        raise AssuranceConflict("incident cannot be resolved from current state")
    row.state = IncidentState.RESOLVED.value
    row.resolved_at = command.resolved_at
    row.resolution_code = _clean(command.resolution_code, "resolution code")
    row.resolution_summary = _clean(command.resolution_summary, "resolution summary")
    _event(
        db,
        row,
        "incident_resolved",
        _clean(command.evidence_ref, "evidence reference"),
        {"resolution_code": row.resolution_code},
        command.resolved_at,
    )
    db.flush()
    return _incident_snapshot(row)


def schedule_maintenance(
    db: Session, *, tenant_id: UUID, command: ScheduleMaintenance
) -> MaintenanceSnapshot:
    if command.ends_at <= command.starts_at:
        raise AssuranceError("maintenance end must follow start")
    row = MaintenanceWindow(
        tenant_id=tenant_id,
        code=_clean(command.code, "maintenance code"),
        summary=_clean(command.summary, "maintenance summary"),
        state=MaintenanceState.SCHEDULED.value,
        starts_at=command.starts_at,
        ends_at=command.ends_at,
        scope_refs=list(command.scope_refs),
        change_ref=command.change_ref,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise AssuranceConflict("maintenance code already exists") from exc
    return MaintenanceSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        summary=row.summary,
        state=MaintenanceState(row.state),
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        scope_refs=tuple(row.scope_refs),
        change_ref=row.change_ref,
    )


def record_notification_evidence(
    db: Session, *, tenant_id: UUID, command: RecordNotificationEvidence
) -> UUID:
    _incident(db, tenant_id, command.incident_id)
    row = NotificationEvidence(
        tenant_id=tenant_id,
        incident_id=command.incident_id,
        subject_ref=_clean(command.subject_ref, "subject reference"),
        channel=_clean(command.channel, "channel"),
        delivery_ref=_clean(command.delivery_ref, "delivery reference"),
        delivered_at=command.delivered_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise AssuranceConflict("notification evidence already exists") from exc
    return row.id


def record_sla_evidence(
    db: Session, *, tenant_id: UUID, command: RecordSlaEvidence
) -> SlaEvidenceSnapshot:
    available = Decimal(command.available_seconds)
    unavailable = Decimal(command.unavailable_seconds)
    if command.period_end <= command.period_start or min(available, unavailable) < 0:
        raise AssuranceError("invalid SLA evidence interval or duration")
    total = available + unavailable
    ratio = Decimal("1") if total == 0 else available / total
    row = SlaEvidence(
        tenant_id=tenant_id,
        subject_ref=_clean(command.subject_ref, "subject reference"),
        period_start=command.period_start,
        period_end=command.period_end,
        available_seconds=available,
        unavailable_seconds=unavailable,
        availability_ratio=ratio,
        source_ref=_clean(command.source_ref, "source reference"),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise AssuranceConflict("SLA evidence already exists") from exc
    return SlaEvidenceSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        period_start=row.period_start,
        period_end=row.period_end,
        available_seconds=row.available_seconds,
        unavailable_seconds=row.unavailable_seconds,
        availability_ratio=row.availability_ratio,
        source_ref=row.source_ref,
    )


def lookup_incidents(
    db: Session, *, tenant_id: UUID, query: IncidentLookup
) -> tuple[IncidentSnapshot, ...]:
    statement = select(Incident).where(Incident.tenant_id == tenant_id)
    if query.incident_id is not None:
        statement = statement.where(Incident.id == query.incident_id)
    if query.code is not None:
        statement = statement.where(Incident.code == query.code)
    if not query.include_resolved:
        statement = statement.where(Incident.state != IncidentState.RESOLVED.value)
    return tuple(_incident_snapshot(row) for row in db.scalars(statement))


def query_impacts(
    db: Session, *, tenant_id: UUID, query: ImpactQuery
) -> tuple[ImpactSnapshot, ...]:
    statement = select(Impact).where(Impact.tenant_id == tenant_id)
    if query.incident_id is not None:
        statement = statement.where(Impact.incident_id == query.incident_id)
    if query.subject_ref is not None:
        statement = statement.where(Impact.subject_ref == query.subject_ref)
    return tuple(_impact_snapshot(row) for row in db.scalars(statement))


def query_maintenance(
    db: Session, *, tenant_id: UUID, query: MaintenanceQuery
) -> tuple[MaintenanceSnapshot, ...]:
    rows = db.scalars(
        select(MaintenanceWindow).where(
            MaintenanceWindow.tenant_id == tenant_id,
            MaintenanceWindow.starts_at <= query.at,
            MaintenanceWindow.ends_at >= query.at,
        )
    )
    return tuple(
        MaintenanceSnapshot(
            id=row.id,
            tenant_id=row.tenant_id,
            code=row.code,
            summary=row.summary,
            state=MaintenanceState(row.state),
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            scope_refs=tuple(row.scope_refs),
            change_ref=row.change_ref,
        )
        for row in rows
        if query.scope_ref is None or query.scope_ref in row.scope_refs
    )


def query_sla_evidence(
    db: Session, *, tenant_id: UUID, query: SlaEvidenceQuery
) -> tuple[SlaEvidenceSnapshot, ...]:
    rows = db.scalars(
        select(SlaEvidence).where(
            SlaEvidence.tenant_id == tenant_id,
            SlaEvidence.subject_ref == query.subject_ref,
            SlaEvidence.period_start >= query.period_start,
            SlaEvidence.period_end <= query.period_end,
        )
    )
    return tuple(
        SlaEvidenceSnapshot(
            id=row.id,
            tenant_id=row.tenant_id,
            subject_ref=row.subject_ref,
            period_start=row.period_start,
            period_end=row.period_end,
            available_seconds=row.available_seconds,
            unavailable_seconds=row.unavailable_seconds,
            availability_ratio=row.availability_ratio,
            source_ref=row.source_ref,
        )
        for row in rows
    )


def recommend_escalation(
    db: Session, *, tenant_id: UUID, incident_id: UUID, evaluated_at: datetime
) -> EscalationRecommendation:
    incident = _incident(db, tenant_id, incident_id)
    severity = ImpactSeverity(incident.severity)
    queue = (
        "network-critical"
        if severity in {ImpactSeverity.CRITICAL, ImpactSeverity.MAJOR}
        else "network-operations"
    )
    return EscalationRecommendation(
        incident_id=incident.id,
        severity=severity,
        recommended_queue=queue,
        reason_code=f"incident-{severity.value}",
        evidence_refs=(incident.detection_ref,),
        evaluated_at=evaluated_at,
    )


__all__ = [
    "AssuranceConflict",
    "AssuranceError",
    "AssuranceNotFound",
    "classify_impact",
    "lookup_incidents",
    "open_incident",
    "query_impacts",
    "query_maintenance",
    "query_sla_evidence",
    "recommend_escalation",
    "record_notification_evidence",
    "record_sla_evidence",
    "resolve_incident",
    "schedule_maintenance",
    "update_incident",
]
