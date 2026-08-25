"""Pure typed contracts for product-neutral position evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID


class PositioningError(ValueError):
    """Base error for a refused positioning operation."""


class PositionObservationRejected(PositioningError):
    """A command or observation violates the supplied contract or policy."""


class PositionObservationConflict(PositioningError):
    """An immutable identity was reused with different evidence."""


class TrackedUnitNotFound(PositioningError):
    """The named opaque tracked unit does not exist in the tenant scope."""


class SourceAssignmentConflict(PositioningError):
    """A source identity overlaps or reuses an assignment incompatibly."""


class GeofenceConflict(PositioningError):
    """A geofence identity was reused with different immutable geometry."""


class ObservationDisposition(StrEnum):
    RECORDED = "recorded"
    REPLAYED = "replayed"
    REJECTED = "rejected"
    CONFLICT = "conflict"


class GeofenceTransition(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class ObservationPolicy:
    """Operational evidence limits resolved by the adopting product."""

    max_batch_size: int
    max_future_skew: timedelta
    max_accuracy_m: float


@dataclass(frozen=True, slots=True)
class ObservationInput:
    client_observation_id: UUID
    source: str
    source_unit_ref: str
    latitude: float
    longitude: float
    accuracy_m: float
    captured_at: datetime
    context_ref: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionGrantInput:
    grant_id: UUID
    tracked_unit_id: UUID
    purpose: str
    granted_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SourceAssignmentInput:
    assignment_id: UUID
    tracked_unit_id: UUID
    source: str
    source_unit_ref: str
    assigned_at: datetime
    unassigned_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CircleFence:
    latitude: float
    longitude: float
    radius_m: float


@dataclass(frozen=True, slots=True)
class PolygonFence:
    points: tuple[tuple[float, float], ...]


GeofenceShape = CircleFence | PolygonFence


@dataclass(frozen=True, slots=True)
class ObservationOutcome:
    client_observation_id: UUID
    disposition: ObservationDisposition
    observation_id: UUID | None = None
    code: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class GeofenceFactOutput:
    id: UUID
    tracked_unit_id: UUID
    geofence_id: UUID
    observation_id: UUID
    transition: GeofenceTransition
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class GeofenceEvaluationInput:
    """Product-selected fences to evaluate against one retained observation."""

    observation_id: UUID
    geofence_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class RecordBatchResult:
    items: tuple[ObservationOutcome, ...]

    @property
    def accepted(self) -> int:
        return sum(
            item.disposition is ObservationDisposition.RECORDED for item in self.items
        )

    @property
    def replayed(self) -> int:
        return sum(
            item.disposition is ObservationDisposition.REPLAYED for item in self.items
        )


@dataclass(frozen=True, slots=True)
class CurrentPositionSnapshot:
    tracked_unit_id: UUID
    observation_id: UUID
    source: str
    source_unit_ref: str
    latitude: float
    longitude: float
    accuracy_m: float
    captured_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class TrailPoint:
    observation_id: UUID
    client_observation_id: UUID
    tracked_unit_id: UUID
    source: str
    source_unit_ref: str
    context_ref: str | None
    latitude: float
    longitude: float
    accuracy_m: float
    captured_at: datetime
    received_at: datetime


__all__ = [
    "CircleFence",
    "CollectionGrantInput",
    "CurrentPositionSnapshot",
    "GeofenceConflict",
    "GeofenceEvaluationInput",
    "GeofenceFactOutput",
    "GeofenceShape",
    "GeofenceTransition",
    "ObservationDisposition",
    "ObservationInput",
    "ObservationOutcome",
    "ObservationPolicy",
    "PolygonFence",
    "PositionObservationConflict",
    "PositionObservationRejected",
    "PositioningError",
    "RecordBatchResult",
    "SourceAssignmentConflict",
    "SourceAssignmentInput",
    "TrackedUnitNotFound",
    "TrailPoint",
]
