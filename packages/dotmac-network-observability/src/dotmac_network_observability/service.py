"""Flush-only collector-neutral observation and health owner."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_network_observability.contracts import (
    AlertQuery,
    AlertSnapshot,
    AlertState,
    AvailabilityState,
    HealthQuery,
    HealthSnapshot,
    MeasurementSnapshot,
    ObservationKind,
    ObservationQuery,
    ObservationReceipt,
    OpenAlertEvidence,
    RebuildHealth,
    RecordAvailability,
    RecordMeasurement,
    RecordObservation,
    ResolveAlertEvidence,
)
from dotmac_network_observability.models import (
    Alert,
    AlertEvidence,
    AvailabilityFact,
    HealthProjection,
    Measurement,
    Observation,
)


class NetworkObservationError(ValueError):
    """Base error for an invalid observation or projection decision."""


class NetworkObservationNotFound(NetworkObservationError):
    """A tenant-local observation aggregate was not found."""


class NetworkObservationConflict(NetworkObservationError):
    """An expected state or immutable observation conflicts."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise NetworkObservationError(f"{label} must not be blank")
    return cleaned


def _stored_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round-trip without weakening ingress.

    Ingress values are tz-aware; PostgreSQL returns them that way and SQLite
    does not. A snapshot built from a freshly written row therefore differed
    from the same snapshot rebuilt on the idempotent replay path, which made a
    correct replay look like a divergence. Normalizing here — the one place
    snapshots are projected from rows — keeps every read tz-aware without
    accepting a naive value at ingress.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _stored_utc_or_none(value: datetime | None) -> datetime | None:
    return None if value is None else _stored_utc(value)


def _alert_snapshot(row: Alert) -> AlertSnapshot:
    return AlertSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        rule_ref=row.rule_ref,
        severity=row.severity,
        state=AlertState(row.state),
        opened_at=_stored_utc(row.opened_at),
        resolved_at=_stored_utc_or_none(row.resolved_at),
        latest_evidence_ref=row.latest_evidence_ref,
    )


def _health_snapshot(row: HealthProjection) -> HealthSnapshot:
    return HealthSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        state=AvailabilityState(row.state),
        reason_code=row.reason_code,
        as_of=row.as_of,
        source_observation_ids=tuple(
            UUID(value) for value in row.source_observation_ids
        ),
        rebuilt_at=row.rebuilt_at,
    )


def record_observation(
    db: Session, *, tenant_id: UUID, command: RecordObservation
) -> ObservationReceipt:
    source_ref = _clean(command.source_ref, "source reference")
    fingerprint = _clean(command.fingerprint, "fingerprint")
    existing = db.scalar(
        select(Observation).where(
            Observation.tenant_id == tenant_id,
            Observation.source_ref == source_ref,
            Observation.fingerprint == fingerprint,
        )
    )
    duplicate = existing is not None
    if existing is None:
        row = Observation(
            tenant_id=tenant_id,
            subject_ref=_clean(command.subject_ref, "subject reference"),
            kind=command.kind.value,
            source_ref=source_ref,
            observed_at=command.observed_at,
            fingerprint=fingerprint,
            attributes=[list(pair) for pair in command.attributes],
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            raise NetworkObservationConflict(
                "observation fingerprint conflicts"
            ) from exc
    else:
        row = existing
        if row.subject_ref != command.subject_ref or row.kind != command.kind.value:
            raise NetworkObservationConflict(
                "fingerprint reused with different observation"
            )
    return ObservationReceipt(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        kind=command.kind,
        source_ref=row.source_ref,
        observed_at=row.observed_at,
        fingerprint=row.fingerprint,
        duplicate=duplicate,
    )


def record_measurement(
    db: Session, *, tenant_id: UUID, command: RecordMeasurement
) -> MeasurementSnapshot:
    source_ref = _clean(command.source_ref, "source reference")
    fingerprint = _clean(command.fingerprint, "fingerprint")
    existing = db.scalar(
        select(Measurement).where(
            Measurement.tenant_id == tenant_id,
            Measurement.source_ref == source_ref,
            Measurement.fingerprint == fingerprint,
        )
    )
    duplicate = existing is not None
    if existing is None:
        row = Measurement(
            tenant_id=tenant_id,
            subject_ref=_clean(command.subject_ref, "subject reference"),
            metric_code=_clean(command.metric_code, "metric code"),
            value=Decimal(command.value),
            unit=_clean(command.unit, "unit"),
            source_ref=source_ref,
            observed_at=command.observed_at,
            fingerprint=fingerprint,
            dimensions=[list(pair) for pair in command.dimensions],
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            raise NetworkObservationConflict(
                "measurement fingerprint conflicts"
            ) from exc
    else:
        row = existing
        if (
            row.subject_ref != command.subject_ref
            or row.metric_code != command.metric_code
            or row.value != command.value
        ):
            raise NetworkObservationConflict(
                "fingerprint reused with different measurement"
            )
    return MeasurementSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        metric_code=row.metric_code,
        value=row.value,
        unit=row.unit,
        source_ref=row.source_ref,
        observed_at=row.observed_at,
        fingerprint=row.fingerprint,
        dimensions=tuple((item[0], item[1]) for item in row.dimensions),
        duplicate=duplicate,
    )


def record_availability(
    db: Session, *, tenant_id: UUID, command: RecordAvailability
) -> UUID:
    row = AvailabilityFact(
        tenant_id=tenant_id,
        subject_ref=_clean(command.subject_ref, "subject reference"),
        state=command.state.value,
        source_ref=_clean(command.source_ref, "source reference"),
        observed_at=command.observed_at,
        reason_code=_clean(command.reason_code, "reason code")
        if command.reason_code
        else None,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise NetworkObservationConflict(
            "availability observation already exists"
        ) from exc
    return row.id


def rebuild_health(
    db: Session, *, tenant_id: UUID, command: RebuildHealth
) -> HealthSnapshot:
    observations = tuple(
        db.scalars(
            select(Observation).where(
                Observation.tenant_id == tenant_id,
                Observation.id.in_(command.source_observation_ids),
                Observation.subject_ref == command.subject_ref,
            )
        )
    )
    if len(observations) != len(set(command.source_observation_ids)):
        raise NetworkObservationConflict(
            "health source observations must exist for the same subject"
        )
    fact = db.scalar(
        select(AvailabilityFact)
        .where(
            AvailabilityFact.tenant_id == tenant_id,
            AvailabilityFact.subject_ref == command.subject_ref,
            AvailabilityFact.observed_at <= command.as_of,
        )
        .order_by(AvailabilityFact.observed_at.desc())
        .limit(1)
    )
    state = AvailabilityState.UNKNOWN if fact is None else AvailabilityState(fact.state)
    reason = None if fact is None else fact.reason_code
    row = db.scalar(
        select(HealthProjection)
        .where(
            HealthProjection.tenant_id == tenant_id,
            HealthProjection.subject_ref == command.subject_ref,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None:
        row = HealthProjection(
            tenant_id=tenant_id,
            subject_ref=_clean(command.subject_ref, "subject reference"),
            state=state.value,
            reason_code=reason,
            as_of=command.as_of,
            source_observation_ids=[
                str(value) for value in command.source_observation_ids
            ],
            rebuilt_at=now,
        )
        db.add(row)
    else:
        row.state = state.value
        row.reason_code = reason
        row.as_of = command.as_of
        row.source_observation_ids = [
            str(value) for value in command.source_observation_ids
        ]
        row.rebuilt_at = now
    db.flush()
    return _health_snapshot(row)


def open_alert_evidence(
    db: Session, *, tenant_id: UUID, command: OpenAlertEvidence
) -> AlertSnapshot:
    subject_ref = _clean(command.subject_ref, "subject reference")
    rule_ref = _clean(command.rule_ref, "rule reference")
    evidence_ref = _clean(command.evidence_ref, "evidence reference")
    row = db.scalar(
        select(Alert)
        .where(
            Alert.tenant_id == tenant_id,
            Alert.subject_ref == subject_ref,
            Alert.rule_ref == rule_ref,
            Alert.state == AlertState.OPEN.value,
        )
        .with_for_update()
    )
    if row is None:
        row = Alert(
            tenant_id=tenant_id,
            subject_ref=subject_ref,
            rule_ref=rule_ref,
            severity=_clean(command.severity, "severity"),
            state=AlertState.OPEN.value,
            opened_at=command.observed_at,
            latest_evidence_ref=evidence_ref,
        )
        try:
            from dotmac_kernel.db import conflict_savepoint

            with conflict_savepoint(db):
                db.add(row)
                db.flush()
                db.add(
                    AlertEvidence(
                        tenant_id=tenant_id,
                        alert_id=row.id,
                        evidence_ref=evidence_ref,
                        event_type="opened",
                        observed_at=command.observed_at,
                    )
                )
                db.flush()
        except IntegrityError as exc:
            raise NetworkObservationConflict("alert was opened concurrently") from exc
        return _alert_snapshot(row)
    else:
        duplicate = db.scalar(
            select(AlertEvidence.id).where(
                AlertEvidence.tenant_id == tenant_id,
                AlertEvidence.alert_id == row.id,
                AlertEvidence.evidence_ref == evidence_ref,
                AlertEvidence.event_type == "opened",
            )
        )
        if duplicate is not None:
            return _alert_snapshot(row)
        try:
            from dotmac_kernel.db import conflict_savepoint

            with conflict_savepoint(db):
                row.latest_evidence_ref = evidence_ref
                db.add(
                    AlertEvidence(
                        tenant_id=tenant_id,
                        alert_id=row.id,
                        evidence_ref=evidence_ref,
                        event_type="opened",
                        observed_at=command.observed_at,
                    )
                )
                db.flush()
        except IntegrityError as exc:
            raise NetworkObservationConflict(
                "alert evidence was recorded concurrently"
            ) from exc
    return _alert_snapshot(row)


def resolve_alert_evidence(
    db: Session, *, tenant_id: UUID, command: ResolveAlertEvidence
) -> AlertSnapshot:
    row = db.scalar(
        select(Alert)
        .where(Alert.tenant_id == tenant_id, Alert.id == command.alert_id)
        .with_for_update()
    )
    if row is None:
        raise NetworkObservationNotFound("alert not found")
    current = AlertState(row.state)
    evidence_ref = _clean(command.evidence_ref, "evidence reference")
    if current is AlertState.RESOLVED and row.latest_evidence_ref == evidence_ref:
        return _alert_snapshot(row)
    if current is not command.expected or current is not AlertState.OPEN:
        raise NetworkObservationConflict("alert state changed")
    try:
        from dotmac_kernel.db import conflict_savepoint

        with conflict_savepoint(db):
            row.state = AlertState.RESOLVED.value
            row.resolved_at = command.observed_at
            row.latest_evidence_ref = evidence_ref
            db.add(
                AlertEvidence(
                    tenant_id=tenant_id,
                    alert_id=row.id,
                    evidence_ref=evidence_ref,
                    event_type="resolved",
                    observed_at=command.observed_at,
                )
            )
            db.flush()
    except IntegrityError as exc:
        raise NetworkObservationConflict("alert resolution evidence conflicts") from exc
    return _alert_snapshot(row)


def query_observations(
    db: Session, *, tenant_id: UUID, query: ObservationQuery
) -> tuple[ObservationReceipt, ...]:
    statement = select(Observation).where(
        Observation.tenant_id == tenant_id, Observation.subject_ref == query.subject_ref
    )
    if query.kind is not None:
        statement = statement.where(Observation.kind == query.kind.value)
    if query.since is not None:
        statement = statement.where(Observation.observed_at >= query.since)
    if query.until is not None:
        statement = statement.where(Observation.observed_at <= query.until)
    return tuple(
        ObservationReceipt(
            id=row.id,
            tenant_id=row.tenant_id,
            subject_ref=row.subject_ref,
            kind=ObservationKind(row.kind),
            source_ref=row.source_ref,
            observed_at=row.observed_at,
            fingerprint=row.fingerprint,
            duplicate=False,
        )
        for row in db.scalars(statement)
    )


def query_health(
    db: Session, *, tenant_id: UUID, query: HealthQuery
) -> HealthSnapshot | None:
    row = db.scalar(
        select(HealthProjection).where(
            HealthProjection.tenant_id == tenant_id,
            HealthProjection.subject_ref == query.subject_ref,
        )
    )
    if row is None or (query.as_of is not None and row.as_of > query.as_of):
        return None
    return _health_snapshot(row)


def query_alerts(
    db: Session, *, tenant_id: UUID, query: AlertQuery
) -> tuple[AlertSnapshot, ...]:
    statement = select(Alert).where(Alert.tenant_id == tenant_id)
    if query.subject_ref is not None:
        statement = statement.where(Alert.subject_ref == query.subject_ref)
    if query.state is not None:
        statement = statement.where(Alert.state == query.state.value)
    return tuple(_alert_snapshot(row) for row in db.scalars(statement))


__all__ = [
    "NetworkObservationConflict",
    "NetworkObservationError",
    "NetworkObservationNotFound",
    "open_alert_evidence",
    "query_alerts",
    "query_health",
    "query_observations",
    "rebuild_health",
    "record_availability",
    "record_measurement",
    "record_observation",
    "resolve_alert_evidence",
]
