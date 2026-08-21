"""Transaction-neutral owner of tracked-unit position evidence and facts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_positioning.contracts import (
    CircleFence,
    CollectionGrantInput,
    CurrentPositionSnapshot,
    GeofenceConflict,
    GeofenceEvaluationInput,
    GeofenceFactOutput,
    GeofenceShape,
    GeofenceTransition,
    ObservationDisposition,
    ObservationInput,
    ObservationOutcome,
    ObservationPolicy,
    PolygonFence,
    PositionObservationConflict,
    PositionObservationRejected,
    RecordBatchResult,
    SourceAssignmentConflict,
    SourceAssignmentInput,
    TrackedUnitNotFound,
    TrailPoint,
)
from dotmac_positioning.models import (
    CollectionGrant,
    CurrentPosition,
    Geofence,
    GeofenceFact,
    GeofenceState,
    PositionObservation,
    SourceAssignment,
    SourceIdentity,
    TrackedUnit,
)


class _RejectedEvidence(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _tenant(scope: TenantScope) -> UUID:
    return scope.tenant_id


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _bounded_text(value: str, *, field: str, maximum: int) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > maximum:
        raise PositionObservationRejected(
            f"{field} must contain 1 to {maximum} characters"
        )
    return normalized


def _optional_context(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if len(normalized) > 128:
        raise _RejectedEvidence(
            "invalid_context",
            "context reference exceeds the storage contract",
        )
    return normalized or None


def _require_policy(policy: ObservationPolicy) -> None:
    if (
        policy.max_batch_size < 1
        or policy.max_future_skew < timedelta(0)
        or policy.max_accuracy_m <= 0
    ):
        raise PositionObservationRejected("observation policy bounds must be positive")


def _require_unit(
    db: Session,
    *,
    tenant_id: UUID,
    tracked_unit_id: UUID,
    lock: bool = False,
) -> TrackedUnit:
    query = select(TrackedUnit).where(
        TrackedUnit.tenant_id == tenant_id,
        TrackedUnit.id == tracked_unit_id,
    )
    if lock:
        query = query.with_for_update()
    row = db.scalars(query).one_or_none()
    if row is None:
        raise TrackedUnitNotFound(f"tracked unit {tracked_unit_id} was not found")
    if not row.is_active:
        raise PositionObservationRejected(f"tracked unit {tracked_unit_id} is inactive")
    return row


def create_tracked_unit(
    db: Session,
    *,
    scope: TenantScope,
    tracked_unit_id: UUID,
    now: datetime,
) -> TrackedUnit:
    """Idempotently establish an opaque unit and its empty current projection."""

    tenant_id = _tenant(scope)
    created_at = _as_utc(now)
    existing = db.scalars(
        select(TrackedUnit).where(
            TrackedUnit.tenant_id == tenant_id,
            TrackedUnit.id == tracked_unit_id,
        )
    ).one_or_none()
    if existing is not None:
        return existing

    from dotmac_kernel.db import conflict_savepoint

    unit = TrackedUnit(
        id=tracked_unit_id,
        tenant_id=tenant_id,
        is_active=True,
        created_at=created_at,
        updated_at=created_at,
    )
    current = CurrentPosition(
        tenant_id=tenant_id,
        tracked_unit_id=tracked_unit_id,
        created_at=created_at,
        updated_at=created_at,
    )
    try:
        with conflict_savepoint(db):
            db.add_all((unit, current))
            db.flush()
    except IntegrityError as exc:
        existing = db.scalars(
            select(TrackedUnit).where(
                TrackedUnit.tenant_id == tenant_id,
                TrackedUnit.id == tracked_unit_id,
            )
        ).one_or_none()
        if existing is None:
            raise PositionObservationConflict(
                f"tracked unit identity {tracked_unit_id} conflicts"
            ) from exc
        return existing
    return unit


def _source_parts(source: str, source_unit_ref: str) -> tuple[str, str]:
    normalized_source = _bounded_text(source, field="source", maximum=32)
    normalized_ref = source_unit_ref.strip()
    if not normalized_ref or len(normalized_ref) > 128:
        raise SourceAssignmentConflict(
            "source unit reference must contain 1 to 128 characters"
        )
    return normalized_source, normalized_ref


def _find_source_identity(
    db: Session,
    *,
    tenant_id: UUID,
    source: str,
    source_unit_ref: str,
    lock: bool = False,
) -> SourceIdentity | None:
    query = select(SourceIdentity).where(
        SourceIdentity.tenant_id == tenant_id,
        SourceIdentity.source == source,
        SourceIdentity.source_unit_ref == source_unit_ref,
    )
    if lock:
        query = query.with_for_update()
    return db.scalars(query).one_or_none()


def _get_or_create_source_identity(
    db: Session,
    *,
    tenant_id: UUID,
    source: str,
    source_unit_ref: str,
) -> SourceIdentity:
    existing = _find_source_identity(
        db,
        tenant_id=tenant_id,
        source=source,
        source_unit_ref=source_unit_ref,
    )
    if existing is None:
        from dotmac_kernel.db import conflict_savepoint

        candidate = SourceIdentity(
            tenant_id=tenant_id,
            source=source,
            source_unit_ref=source_unit_ref,
        )
        try:
            with conflict_savepoint(db):
                db.add(candidate)
                db.flush()
        except IntegrityError:
            existing = _find_source_identity(
                db,
                tenant_id=tenant_id,
                source=source,
                source_unit_ref=source_unit_ref,
            )
            if existing is None:
                raise
        else:
            existing = candidate
    locked = db.scalars(
        select(SourceIdentity)
        .where(
            SourceIdentity.tenant_id == tenant_id,
            SourceIdentity.id == existing.id,
        )
        .with_for_update()
    ).one()
    return locked


def _require_assignment_replay(
    db: Session,
    *,
    tenant_id: UUID,
    existing: SourceAssignment,
    assignment: SourceAssignmentInput,
    source: str,
    source_ref: str,
    assigned_at: datetime,
    unassigned_at: datetime | None,
) -> SourceAssignment:
    identity = db.scalars(
        select(SourceIdentity).where(
            SourceIdentity.tenant_id == tenant_id,
            SourceIdentity.id == existing.source_identity_id,
        )
    ).one()
    expected = (
        assignment.tracked_unit_id,
        source,
        source_ref,
        assigned_at,
        unassigned_at,
    )
    actual = (
        existing.tracked_unit_id,
        identity.source,
        identity.source_unit_ref,
        _as_utc(existing.assigned_at),
        _as_utc(existing.unassigned_at) if existing.unassigned_at is not None else None,
    )
    if actual != expected:
        raise SourceAssignmentConflict(
            f"source assignment identity {assignment.assignment_id} was reused"
        )
    return existing


def assign_source(
    db: Session,
    *,
    scope: TenantScope,
    assignment: SourceAssignmentInput,
) -> SourceAssignment:
    """Bind an open source identity to one unit for an explicit time range."""

    tenant_id = _tenant(scope)
    _require_unit(
        db,
        tenant_id=tenant_id,
        tracked_unit_id=assignment.tracked_unit_id,
    )
    source, source_ref = _source_parts(assignment.source, assignment.source_unit_ref)
    assigned_at = _as_utc(assignment.assigned_at)
    unassigned_at = (
        _as_utc(assignment.unassigned_at)
        if assignment.unassigned_at is not None
        else None
    )
    if unassigned_at is not None and unassigned_at <= assigned_at:
        raise SourceAssignmentConflict("source assignment must end after it starts")

    existing = db.scalars(
        select(SourceAssignment).where(
            SourceAssignment.tenant_id == tenant_id,
            SourceAssignment.id == assignment.assignment_id,
        )
    ).one_or_none()
    if existing is not None:
        return _require_assignment_replay(
            db,
            tenant_id=tenant_id,
            existing=existing,
            assignment=assignment,
            source=source,
            source_ref=source_ref,
            assigned_at=assigned_at,
            unassigned_at=unassigned_at,
        )

    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            identity = _get_or_create_source_identity(
                db,
                tenant_id=tenant_id,
                source=source,
                source_unit_ref=source_ref,
            )
            existing = db.scalars(
                select(SourceAssignment).where(
                    SourceAssignment.tenant_id == tenant_id,
                    SourceAssignment.id == assignment.assignment_id,
                )
            ).one_or_none()
            if existing is not None:
                return _require_assignment_replay(
                    db,
                    tenant_id=tenant_id,
                    existing=existing,
                    assignment=assignment,
                    source=source,
                    source_ref=source_ref,
                    assigned_at=assigned_at,
                    unassigned_at=unassigned_at,
                )

            overlap_conditions = [
                SourceAssignment.tenant_id == tenant_id,
                SourceAssignment.source_identity_id == identity.id,
                or_(
                    SourceAssignment.unassigned_at.is_(None),
                    SourceAssignment.unassigned_at > assigned_at,
                ),
            ]
            if unassigned_at is not None:
                overlap_conditions.append(SourceAssignment.assigned_at < unassigned_at)
            overlap = db.scalars(
                select(SourceAssignment).where(*overlap_conditions)
            ).first()
            if overlap is not None:
                raise SourceAssignmentConflict(
                    f"source identity {source}/{source_ref} already has an "
                    "overlapping assignment"
                )

            row = SourceAssignment(
                id=assignment.assignment_id,
                tenant_id=tenant_id,
                tracked_unit_id=assignment.tracked_unit_id,
                source_identity_id=identity.id,
                assigned_at=assigned_at,
                unassigned_at=unassigned_at,
            )
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalars(
            select(SourceAssignment).where(
                SourceAssignment.tenant_id == tenant_id,
                SourceAssignment.id == assignment.assignment_id,
            )
        ).one_or_none()
        if winner is None:
            raise SourceAssignmentConflict(
                "source assignment conflicted outside its declared identity"
            ) from exc
        return _require_assignment_replay(
            db,
            tenant_id=tenant_id,
            existing=winner,
            assignment=assignment,
            source=source,
            source_ref=source_ref,
            assigned_at=assigned_at,
            unassigned_at=unassigned_at,
        )
    return row


def unassign_source(
    db: Session,
    *,
    scope: TenantScope,
    assignment_id: UUID,
    unassigned_at: datetime,
) -> SourceAssignment:
    tenant_id = _tenant(scope)
    candidate = db.scalars(
        select(SourceAssignment).where(
            SourceAssignment.tenant_id == tenant_id,
            SourceAssignment.id == assignment_id,
        )
    ).one_or_none()
    if candidate is None:
        raise SourceAssignmentConflict(
            f"source assignment {assignment_id} was not found"
        )
    db.scalars(
        select(SourceIdentity)
        .where(
            SourceIdentity.tenant_id == tenant_id,
            SourceIdentity.id == candidate.source_identity_id,
        )
        .with_for_update()
    ).one()
    row = db.scalars(
        select(SourceAssignment)
        .where(
            SourceAssignment.tenant_id == tenant_id,
            SourceAssignment.id == assignment_id,
        )
        .with_for_update()
    ).one()
    ended_at = _as_utc(unassigned_at)
    if ended_at <= _as_utc(row.assigned_at):
        raise SourceAssignmentConflict("source assignment must end after it starts")
    if row.unassigned_at is None:
        row.unassigned_at = ended_at
        db.flush()
    elif _as_utc(row.unassigned_at) != ended_at:
        raise SourceAssignmentConflict(
            f"source assignment {assignment_id} already ended at another time"
        )
    return row


def resolve_tracked_unit(
    db: Session,
    *,
    scope: TenantScope,
    source: str,
    source_unit_ref: str,
    at: datetime,
) -> UUID | None:
    tenant_id = _tenant(scope)
    normalized_source = _bounded_text(source, field="source", maximum=32)
    normalized_ref = source_unit_ref.strip()
    identity = _find_source_identity(
        db,
        tenant_id=tenant_id,
        source=normalized_source,
        source_unit_ref=normalized_ref,
    )
    if identity is None:
        return None
    instant = _as_utc(at)
    return db.scalars(
        select(SourceAssignment.tracked_unit_id)
        .where(
            SourceAssignment.tenant_id == tenant_id,
            SourceAssignment.source_identity_id == identity.id,
            SourceAssignment.assigned_at <= instant,
            or_(
                SourceAssignment.unassigned_at.is_(None),
                SourceAssignment.unassigned_at > instant,
            ),
        )
        .order_by(SourceAssignment.assigned_at.desc())
    ).first()


def _require_collection_grant_replay(
    existing: CollectionGrant,
    *,
    grant: CollectionGrantInput,
    purpose: str,
    granted_at: datetime,
    expires_at: datetime,
) -> CollectionGrant:
    expected = (grant.tracked_unit_id, purpose, granted_at, expires_at)
    actual = (
        existing.tracked_unit_id,
        existing.purpose,
        _as_utc(existing.granted_at),
        _as_utc(existing.expires_at),
    )
    if actual != expected:
        raise PositionObservationConflict(
            f"collection grant identity {grant.grant_id} was reused"
        )
    return existing


def grant_collection(
    db: Session,
    *,
    scope: TenantScope,
    grant: CollectionGrantInput,
) -> CollectionGrant:
    tenant_id = _tenant(scope)
    _require_unit(db, tenant_id=tenant_id, tracked_unit_id=grant.tracked_unit_id)
    purpose = _bounded_text(grant.purpose, field="purpose", maximum=32)
    granted_at = _as_utc(grant.granted_at)
    expires_at = _as_utc(grant.expires_at)
    if expires_at <= granted_at:
        raise PositionObservationRejected(
            "collection grant expiry must follow grant time"
        )

    existing = db.scalars(
        select(CollectionGrant).where(
            CollectionGrant.tenant_id == tenant_id,
            CollectionGrant.id == grant.grant_id,
        )
    ).one_or_none()
    if existing is not None:
        return _require_collection_grant_replay(
            existing,
            grant=grant,
            purpose=purpose,
            granted_at=granted_at,
            expires_at=expires_at,
        )

    row = CollectionGrant(
        id=grant.grant_id,
        tenant_id=tenant_id,
        tracked_unit_id=grant.tracked_unit_id,
        purpose=purpose,
        granted_at=granted_at,
        expires_at=expires_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalars(
            select(CollectionGrant).where(
                CollectionGrant.tenant_id == tenant_id,
                CollectionGrant.id == grant.grant_id,
            )
        ).one_or_none()
        if winner is None:
            raise PositionObservationConflict(
                "collection grant conflicted outside its declared identity"
            ) from exc
        return _require_collection_grant_replay(
            winner,
            grant=grant,
            purpose=purpose,
            granted_at=granted_at,
            expires_at=expires_at,
        )
    return row


def revoke_collection(
    db: Session,
    *,
    scope: TenantScope,
    tracked_unit_id: UUID,
    purpose: str,
    revoked_at: datetime,
) -> int:
    tenant_id = _tenant(scope)
    normalized_purpose = _bounded_text(purpose, field="purpose", maximum=32)
    instant = _as_utc(revoked_at)
    rows = list(
        db.scalars(
            select(CollectionGrant)
            .where(
                CollectionGrant.tenant_id == tenant_id,
                CollectionGrant.tracked_unit_id == tracked_unit_id,
                CollectionGrant.purpose == normalized_purpose,
                CollectionGrant.granted_at <= instant,
                or_(
                    CollectionGrant.revoked_at.is_(None),
                    CollectionGrant.revoked_at > instant,
                ),
            )
            .with_for_update()
        )
    )
    for row in rows:
        row.revoked_at = instant
    if rows:
        db.flush()
    return len(rows)


def _has_collection_grant(
    db: Session,
    *,
    tenant_id: UUID,
    tracked_unit_id: UUID,
    purpose: str,
    at: datetime,
) -> bool:
    return (
        db.scalars(
            select(CollectionGrant.id).where(
                CollectionGrant.tenant_id == tenant_id,
                CollectionGrant.tracked_unit_id == tracked_unit_id,
                CollectionGrant.purpose == purpose,
                CollectionGrant.granted_at <= at,
                CollectionGrant.expires_at > at,
                or_(
                    CollectionGrant.revoked_at.is_(None),
                    CollectionGrant.revoked_at > at,
                ),
            )
        ).first()
        is not None
    )


def _validate_observation(
    observation: ObservationInput,
    *,
    received_at: datetime,
    policy: ObservationPolicy,
) -> tuple[str, str, str | None, datetime]:
    if not -90 <= observation.latitude <= 90:
        raise _RejectedEvidence("invalid_coordinates", "latitude is out of range")
    if not -180 <= observation.longitude <= 180:
        raise _RejectedEvidence("invalid_coordinates", "longitude is out of range")
    if not 0 <= observation.accuracy_m <= policy.max_accuracy_m:
        raise _RejectedEvidence("invalid_accuracy", "accuracy is out of range")
    captured_at = _as_utc(observation.captured_at)
    if captured_at > received_at + policy.max_future_skew:
        raise _RejectedEvidence(
            "future_timestamp", "capture time is beyond the accepted clock skew"
        )
    source = observation.source.strip().lower()
    if not source or len(source) > 32:
        raise _RejectedEvidence(
            "invalid_source", "source must contain 1 to 32 characters"
        )
    source_unit_ref = observation.source_unit_ref.strip()
    if not source_unit_ref or len(source_unit_ref) > 128:
        raise _RejectedEvidence(
            "invalid_source_identity",
            "source unit reference must contain 1 to 128 characters",
        )
    return (
        source,
        source_unit_ref,
        _optional_context(observation.context_ref),
        captured_at,
    )


def _fingerprint(
    observation: ObservationInput,
    *,
    tracked_unit_id: UUID,
    source: str,
    source_unit_ref: str,
    context_ref: str | None,
    captured_at: datetime,
) -> str:
    payload = {
        "accuracy_m": float(observation.accuracy_m),
        "captured_at": captured_at.isoformat(),
        "context_ref": context_ref,
        "latitude": float(observation.latitude),
        "longitude": float(observation.longitude),
        "source": source,
        "source_unit_ref": source_unit_ref,
        "tracked_unit_id": str(tracked_unit_id),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _existing_observation(
    db: Session,
    *,
    tenant_id: UUID,
    source_identity_id: UUID,
    client_observation_id: UUID,
) -> PositionObservation | None:
    return db.scalars(
        select(PositionObservation).where(
            PositionObservation.tenant_id == tenant_id,
            PositionObservation.source_identity_id == source_identity_id,
            PositionObservation.client_observation_id == client_observation_id,
        )
    ).one_or_none()


def _position_rank(
    *,
    captured_at: datetime,
    accuracy_m: float,
    received_at: datetime,
    observation_id: UUID,
) -> tuple[datetime, float, datetime, int]:
    """One ordering shared by live projection and deterministic repair."""

    return (
        _as_utc(captured_at),
        -float(accuracy_m),
        _as_utc(received_at),
        observation_id.int,
    )


def _observation_advances_current(
    current: CurrentPosition,
    observation: PositionObservation,
) -> bool:
    if current.observation_id is None:
        return True
    if current.captured_at is None or current.accuracy_m is None:
        raise PositionObservationConflict("current position projection is incomplete")
    if current.received_at is None:
        raise PositionObservationConflict("current position projection is incomplete")
    return _position_rank(
        captured_at=observation.captured_at,
        accuracy_m=observation.accuracy_m,
        received_at=observation.received_at,
        observation_id=observation.id,
    ) > _position_rank(
        captured_at=current.captured_at,
        accuracy_m=current.accuracy_m,
        received_at=current.received_at,
        observation_id=current.observation_id,
    )


def _apply_current_observation(
    current: CurrentPosition,
    observation: PositionObservation,
) -> None:
    current.observation_id = observation.id
    current.source_identity_id = observation.source_identity_id
    current.source = observation.source
    current.source_unit_ref = observation.source_unit_ref
    current.latitude = observation.latitude
    current.longitude = observation.longitude
    current.accuracy_m = observation.accuracy_m
    current.captured_at = observation.captured_at
    current.received_at = observation.received_at


def _record_one(
    db: Session,
    *,
    tenant_id: UUID,
    tracked_unit_id: UUID,
    observation: ObservationInput,
    policy: ObservationPolicy,
    received_at: datetime,
) -> ObservationOutcome:
    try:
        source, source_unit_ref, context_ref, captured_at = _validate_observation(
            observation,
            received_at=received_at,
            policy=policy,
        )
    except _RejectedEvidence as exc:
        return ObservationOutcome(
            client_observation_id=observation.client_observation_id,
            disposition=ObservationDisposition.REJECTED,
            code=exc.code,
            detail=exc.detail,
        )
    identity = _find_source_identity(
        db,
        tenant_id=tenant_id,
        source=source,
        source_unit_ref=source_unit_ref,
    )
    if identity is None:
        return ObservationOutcome(
            client_observation_id=observation.client_observation_id,
            disposition=ObservationDisposition.REJECTED,
            code="source_not_assigned",
            detail="source identity has no tracked-unit assignment",
        )
    fingerprint = _fingerprint(
        observation,
        tracked_unit_id=tracked_unit_id,
        source=source,
        source_unit_ref=source_unit_ref,
        context_ref=context_ref,
        captured_at=captured_at,
    )
    existing = _existing_observation(
        db,
        tenant_id=tenant_id,
        source_identity_id=identity.id,
        client_observation_id=observation.client_observation_id,
    )
    if existing is not None:
        if existing.payload_fingerprint != fingerprint:
            return ObservationOutcome(
                client_observation_id=observation.client_observation_id,
                disposition=ObservationDisposition.CONFLICT,
                observation_id=existing.id,
                code="identity_collision",
                detail="observation identity was reused with different evidence",
            )
        return ObservationOutcome(
            client_observation_id=observation.client_observation_id,
            disposition=ObservationDisposition.REPLAYED,
            observation_id=existing.id,
        )

    assigned_unit_id = db.scalars(
        select(SourceAssignment.tracked_unit_id)
        .where(
            SourceAssignment.tenant_id == tenant_id,
            SourceAssignment.source_identity_id == identity.id,
            SourceAssignment.assigned_at <= captured_at,
            or_(
                SourceAssignment.unassigned_at.is_(None),
                SourceAssignment.unassigned_at > captured_at,
            ),
        )
        .order_by(SourceAssignment.assigned_at.desc())
    ).first()
    if assigned_unit_id != tracked_unit_id:
        return ObservationOutcome(
            client_observation_id=observation.client_observation_id,
            disposition=ObservationDisposition.REJECTED,
            code="source_not_assigned",
            detail="source identity was not assigned to the tracked unit",
        )

    from dotmac_kernel.db import conflict_savepoint

    row = PositionObservation(
        tenant_id=tenant_id,
        tracked_unit_id=tracked_unit_id,
        source_identity_id=identity.id,
        client_observation_id=observation.client_observation_id,
        payload_fingerprint=fingerprint,
        source=source,
        source_unit_ref=source_unit_ref,
        context_ref=context_ref,
        latitude=float(observation.latitude),
        longitude=float(observation.longitude),
        accuracy_m=float(observation.accuracy_m),
        captured_at=captured_at,
        received_at=received_at,
    )
    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = _existing_observation(
            db,
            tenant_id=tenant_id,
            source_identity_id=identity.id,
            client_observation_id=observation.client_observation_id,
        )
        if winner is None:
            raise PositionObservationConflict(
                "position observation conflicted outside its declared identity"
            ) from exc
        if winner.payload_fingerprint != fingerprint:
            return ObservationOutcome(
                client_observation_id=observation.client_observation_id,
                disposition=ObservationDisposition.CONFLICT,
                observation_id=winner.id,
                code="identity_collision",
                detail="observation identity was reused with different evidence",
            )
        return ObservationOutcome(
            client_observation_id=observation.client_observation_id,
            disposition=ObservationDisposition.REPLAYED,
            observation_id=winner.id,
        )

    current = db.scalars(
        select(CurrentPosition)
        .where(
            CurrentPosition.tenant_id == tenant_id,
            CurrentPosition.tracked_unit_id == tracked_unit_id,
        )
        .with_for_update()
    ).one()
    if _observation_advances_current(current, row):
        _apply_current_observation(current, row)
        db.flush()
    return ObservationOutcome(
        client_observation_id=observation.client_observation_id,
        disposition=ObservationDisposition.RECORDED,
        observation_id=row.id,
    )


def record_observations(
    db: Session,
    *,
    scope: TenantScope,
    tracked_unit_id: UUID,
    purpose: str,
    policy: ObservationPolicy,
    received_at: datetime,
    observations: tuple[ObservationInput, ...],
) -> RecordBatchResult:
    """Validate and stage one batch inside the caller's current transaction."""

    _require_policy(policy)
    if not observations:
        raise PositionObservationRejected("observation batch cannot be empty")
    if len(observations) > policy.max_batch_size:
        raise PositionObservationRejected("observation batch exceeds product policy")
    tenant_id = _tenant(scope)
    _require_unit(db, tenant_id=tenant_id, tracked_unit_id=tracked_unit_id, lock=True)
    normalized_purpose = _bounded_text(purpose, field="purpose", maximum=32)
    received = _as_utc(received_at)
    if not _has_collection_grant(
        db,
        tenant_id=tenant_id,
        tracked_unit_id=tracked_unit_id,
        purpose=normalized_purpose,
        at=received,
    ):
        raise PositionObservationRejected(
            "an active collection grant for the requested purpose is required"
        )

    items: list[ObservationOutcome] = []
    for observation in observations:
        outcome = _record_one(
            db,
            tenant_id=tenant_id,
            tracked_unit_id=tracked_unit_id,
            observation=observation,
            policy=policy,
            received_at=received,
        )
        items.append(outcome)
    return RecordBatchResult(items=tuple(items))


def get_current_position(
    db: Session,
    *,
    scope: TenantScope,
    tracked_unit_id: UUID,
) -> CurrentPositionSnapshot | None:
    row = db.scalars(
        select(CurrentPosition).where(
            CurrentPosition.tenant_id == _tenant(scope),
            CurrentPosition.tracked_unit_id == tracked_unit_id,
        )
    ).one_or_none()
    if row is None or row.observation_id is None:
        return None
    source = row.source
    source_unit_ref = row.source_unit_ref
    latitude = row.latitude
    longitude = row.longitude
    accuracy_m = row.accuracy_m
    captured_at = row.captured_at
    received_at = row.received_at
    if (
        source is None
        or source_unit_ref is None
        or latitude is None
        or longitude is None
        or accuracy_m is None
        or captured_at is None
        or received_at is None
    ):
        raise PositionObservationConflict("current position projection is incomplete")
    return CurrentPositionSnapshot(
        tracked_unit_id=row.tracked_unit_id,
        observation_id=row.observation_id,
        source=str(source),
        source_unit_ref=str(source_unit_ref),
        latitude=float(latitude),
        longitude=float(longitude),
        accuracy_m=float(accuracy_m),
        captured_at=_as_utc(captured_at),
        received_at=_as_utc(received_at),
    )


def rebuild_current_position(
    db: Session,
    *,
    scope: TenantScope,
    tracked_unit_id: UUID,
) -> CurrentPositionSnapshot | None:
    """Idempotently repair one current projection from retained evidence."""

    tenant_id = _tenant(scope)
    _require_unit(
        db,
        tenant_id=tenant_id,
        tracked_unit_id=tracked_unit_id,
        lock=True,
    )
    current = db.scalars(
        select(CurrentPosition)
        .where(
            CurrentPosition.tenant_id == tenant_id,
            CurrentPosition.tracked_unit_id == tracked_unit_id,
        )
        .with_for_update()
    ).one()
    winner = db.scalars(
        select(PositionObservation)
        .where(
            PositionObservation.tenant_id == tenant_id,
            PositionObservation.tracked_unit_id == tracked_unit_id,
        )
        .order_by(
            PositionObservation.captured_at.desc(),
            PositionObservation.accuracy_m.asc(),
            PositionObservation.received_at.desc(),
            PositionObservation.id.desc(),
        )
        .limit(1)
    ).one_or_none()
    if winner is None:
        current.observation_id = None
        current.source_identity_id = None
        current.source = None
        current.source_unit_ref = None
        current.latitude = None
        current.longitude = None
        current.accuracy_m = None
        current.captured_at = None
        current.received_at = None
        db.flush()
        return None
    _apply_current_observation(current, winner)
    db.flush()
    return get_current_position(
        db,
        scope=scope,
        tracked_unit_id=tracked_unit_id,
    )


def get_trail(
    db: Session,
    *,
    scope: TenantScope,
    tracked_unit_id: UUID,
    limit: int,
) -> tuple[TrailPoint, ...]:
    if limit < 1:
        raise PositionObservationRejected("trail limit must be positive")
    rows = db.scalars(
        select(PositionObservation)
        .where(
            PositionObservation.tenant_id == _tenant(scope),
            PositionObservation.tracked_unit_id == tracked_unit_id,
        )
        .order_by(
            PositionObservation.captured_at.desc(),
            PositionObservation.received_at.desc(),
            PositionObservation.id.desc(),
        )
        .limit(limit)
    )
    return tuple(
        TrailPoint(
            observation_id=row.id,
            client_observation_id=row.client_observation_id,
            tracked_unit_id=row.tracked_unit_id,
            source=row.source,
            source_unit_ref=row.source_unit_ref,
            context_ref=row.context_ref,
            latitude=row.latitude,
            longitude=row.longitude,
            accuracy_m=row.accuracy_m,
            captured_at=_as_utc(row.captured_at),
            received_at=_as_utc(row.received_at),
        )
        for row in rows
    )


def prune_observations(
    db: Session,
    *,
    scope: TenantScope,
    received_before: datetime,
    preserve_observation_ids: frozenset[UUID] = frozenset(),
) -> int:
    """Delete expired evidence except current or product-preserved rows."""

    tenant_id = _tenant(scope)
    current_ids = select(CurrentPosition.observation_id).where(
        CurrentPosition.tenant_id == tenant_id,
        CurrentPosition.observation_id.is_not(None),
    )
    query = db.query(PositionObservation).filter(
        PositionObservation.tenant_id == tenant_id,
        PositionObservation.received_at < _as_utc(received_before),
        PositionObservation.id.not_in(current_ids),
    )
    if preserve_observation_ids:
        query = query.filter(
            PositionObservation.id.not_in(tuple(preserve_observation_ids))
        )
    deleted = query.delete(synchronize_session=False)
    return int(deleted or 0)


def _validate_point(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise GeofenceConflict("geofence point is outside geographic bounds")


def _shape_values(shape: GeofenceShape) -> dict[str, object]:
    if isinstance(shape, CircleFence):
        _validate_point(shape.latitude, shape.longitude)
        if shape.radius_m <= 0:
            raise GeofenceConflict("circle radius must be positive")
        return {
            "shape_kind": "circle",
            "center_latitude": float(shape.latitude),
            "center_longitude": float(shape.longitude),
            "radius_m": float(shape.radius_m),
            "polygon_points": None,
        }
    if isinstance(shape, PolygonFence):
        if len(shape.points) < 3:
            raise GeofenceConflict("polygon requires at least three points")
        for latitude, longitude in shape.points:
            _validate_point(latitude, longitude)
        return {
            "shape_kind": "polygon",
            "center_latitude": None,
            "center_longitude": None,
            "radius_m": None,
            "polygon_points": [
                [float(latitude), float(longitude)]
                for latitude, longitude in shape.points
            ],
        }
    raise TypeError(f"unsupported geofence shape {type(shape).__name__}")


def _require_geofence_replay(
    existing: Geofence,
    *,
    geofence_id: UUID,
    values: dict[str, object],
) -> Geofence:
    actual = {
        "shape_kind": existing.shape_kind,
        "center_latitude": existing.center_latitude,
        "center_longitude": existing.center_longitude,
        "radius_m": existing.radius_m,
        "polygon_points": existing.polygon_points,
    }
    if actual != values:
        raise GeofenceConflict(f"geofence identity {geofence_id} was reused")
    return existing


def create_geofence(
    db: Session,
    *,
    scope: TenantScope,
    geofence_id: UUID,
    shape: GeofenceShape,
    now: datetime,
) -> Geofence:
    tenant_id = _tenant(scope)
    created_at = _as_utc(now)
    values = _shape_values(shape)
    existing = db.scalars(
        select(Geofence).where(
            Geofence.tenant_id == tenant_id,
            Geofence.id == geofence_id,
        )
    ).one_or_none()
    if existing is not None:
        return _require_geofence_replay(
            existing,
            geofence_id=geofence_id,
            values=values,
        )
    row = Geofence(
        id=geofence_id,
        tenant_id=tenant_id,
        is_active=True,
        created_at=created_at,
        updated_at=created_at,
        **values,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalars(
            select(Geofence).where(
                Geofence.tenant_id == tenant_id,
                Geofence.id == geofence_id,
            )
        ).one_or_none()
        if winner is None:
            raise GeofenceConflict(
                "geofence conflicted outside its declared identity"
            ) from exc
        return _require_geofence_replay(
            winner,
            geofence_id=geofence_id,
            values=values,
        )
    return row


def deactivate_geofence(
    db: Session,
    *,
    scope: TenantScope,
    geofence_id: UUID,
) -> Geofence:
    row = db.scalars(
        select(Geofence)
        .where(
            Geofence.tenant_id == _tenant(scope),
            Geofence.id == geofence_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise GeofenceConflict(f"geofence {geofence_id} was not found")
    if row.is_active:
        row.is_active = False
        db.flush()
    return row


def _haversine_m(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    earth_radius_m = 6_371_000.0
    phi_a = math.radians(latitude_a)
    phi_b = math.radians(latitude_b)
    delta_phi = math.radians(latitude_b - latitude_a)
    delta_lambda = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * earth_radius_m * math.asin(min(1.0, math.sqrt(value)))


def _inside_polygon(
    latitude: float,
    longitude: float,
    points: list[list[float]],
) -> bool:
    inside = False
    previous = points[-1]
    for current in points:
        current_y, current_x = current
        previous_y, previous_x = previous
        crosses = (current_y > latitude) != (previous_y > latitude)
        if crosses:
            crossing_x = (previous_x - current_x) * (latitude - current_y) / (
                previous_y - current_y
            ) + current_x
            if longitude < crossing_x:
                inside = not inside
        previous = current
    return inside


def _inside_geofence(row: Geofence, observation: PositionObservation) -> bool:
    if row.shape_kind == "circle":
        if (
            row.center_latitude is None
            or row.center_longitude is None
            or row.radius_m is None
        ):
            raise GeofenceConflict(f"circle geofence {row.id} is incomplete")
        return (
            _haversine_m(
                row.center_latitude,
                row.center_longitude,
                observation.latitude,
                observation.longitude,
            )
            <= row.radius_m
        )
    if row.shape_kind == "polygon" and row.polygon_points is not None:
        return _inside_polygon(
            observation.latitude,
            observation.longitude,
            row.polygon_points,
        )
    raise GeofenceConflict(f"geofence {row.id} has an unsupported shape")


def _fact_output(row: GeofenceFact) -> GeofenceFactOutput:
    return GeofenceFactOutput(
        id=row.id,
        tracked_unit_id=row.tracked_unit_id,
        geofence_id=row.geofence_id,
        observation_id=row.observation_id,
        transition=GeofenceTransition(row.transition),
        occurred_at=_as_utc(row.occurred_at),
    )


def _facts_for_observation(
    db: Session,
    *,
    tenant_id: UUID,
    observation_id: UUID,
    geofence_ids: tuple[UUID, ...],
) -> tuple[GeofenceFactOutput, ...]:
    rows = db.scalars(
        select(GeofenceFact).where(
            GeofenceFact.tenant_id == tenant_id,
            GeofenceFact.observation_id == observation_id,
            GeofenceFact.geofence_id.in_(geofence_ids),
        )
    )
    return tuple(_fact_output(row) for row in rows)


def _evaluate_selected_geofences(
    db: Session,
    *,
    observation: PositionObservation,
    fences: tuple[Geofence, ...],
) -> tuple[GeofenceFactOutput, ...]:
    for fence in fences:
        inside = _inside_geofence(fence, observation)
        state = db.scalars(
            select(GeofenceState)
            .where(
                GeofenceState.tenant_id == observation.tenant_id,
                GeofenceState.tracked_unit_id == observation.tracked_unit_id,
                GeofenceState.geofence_id == fence.id,
            )
            .with_for_update()
        ).one_or_none()
        transition: GeofenceTransition | None = None
        if state is None:
            state = GeofenceState(
                tenant_id=observation.tenant_id,
                tracked_unit_id=observation.tracked_unit_id,
                geofence_id=fence.id,
                is_inside=inside,
                last_observation_id=observation.id,
                evaluated_at=observation.captured_at,
            )
            db.add(state)
            if inside:
                transition = GeofenceTransition.ENTRY
        else:
            if state.is_inside != inside:
                transition = (
                    GeofenceTransition.ENTRY if inside else GeofenceTransition.EXIT
                )
            state.is_inside = inside
            state.last_observation_id = observation.id
            state.evaluated_at = observation.captured_at
        if transition is not None:
            fact = GeofenceFact(
                tenant_id=observation.tenant_id,
                tracked_unit_id=observation.tracked_unit_id,
                geofence_id=fence.id,
                observation_id=observation.id,
                transition=str(transition),
                occurred_at=observation.captured_at,
            )
            db.add(fact)
            db.flush()
    db.flush()
    return _facts_for_observation(
        db,
        tenant_id=observation.tenant_id,
        observation_id=observation.id,
        geofence_ids=tuple(fence.id for fence in fences),
    )


def evaluate_geofences(
    db: Session,
    *,
    scope: TenantScope,
    evaluation: GeofenceEvaluationInput,
) -> tuple[GeofenceFactOutput, ...]:
    """Evaluate only the fences selected by the adopting product.

    The selection is authoritative product input, never inferred from every
    active tenant fence. Only the observation backing the current projection
    may advance geofence state, so late evidence cannot move state backwards.
    """

    tenant_id = _tenant(scope)
    geofence_ids = tuple(dict.fromkeys(evaluation.geofence_ids))
    if not geofence_ids:
        return ()
    observation = db.scalars(
        select(PositionObservation).where(
            PositionObservation.tenant_id == tenant_id,
            PositionObservation.id == evaluation.observation_id,
        )
    ).one_or_none()
    if observation is None:
        raise GeofenceConflict(
            f"position observation {evaluation.observation_id} was not found"
        )

    _require_unit(
        db,
        tenant_id=tenant_id,
        tracked_unit_id=observation.tracked_unit_id,
        lock=True,
    )
    fences_by_id = {
        fence.id: fence
        for fence in db.scalars(
            select(Geofence).where(
                Geofence.tenant_id == tenant_id,
                Geofence.id.in_(geofence_ids),
                Geofence.is_active.is_(True),
            )
        )
    }
    missing = tuple(
        fence_id for fence_id in geofence_ids if fence_id not in fences_by_id
    )
    if missing:
        raise GeofenceConflict(
            "selected geofence ids are missing or inactive: "
            + ", ".join(str(fence_id) for fence_id in missing)
        )
    current_observation_id = db.scalars(
        select(CurrentPosition.observation_id)
        .where(
            CurrentPosition.tenant_id == tenant_id,
            CurrentPosition.tracked_unit_id == observation.tracked_unit_id,
        )
        .with_for_update()
    ).one()
    if current_observation_id != observation.id:
        return _facts_for_observation(
            db,
            tenant_id=tenant_id,
            observation_id=observation.id,
            geofence_ids=geofence_ids,
        )
    return _evaluate_selected_geofences(
        db,
        observation=observation,
        fences=tuple(fences_by_id[fence_id] for fence_id in geofence_ids),
    )


__all__ = [
    "assign_source",
    "create_geofence",
    "create_tracked_unit",
    "deactivate_geofence",
    "evaluate_geofences",
    "get_current_position",
    "get_trail",
    "grant_collection",
    "prune_observations",
    "rebuild_current_position",
    "record_observations",
    "resolve_tracked_unit",
    "revoke_collection",
    "unassign_source",
]
