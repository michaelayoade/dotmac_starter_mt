"""Provider-neutral inputs and views for platform health."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HealthObservationInput:
    source_ref: str
    observation_key: str
    component_code: str
    state: HealthState
    observed_at: datetime
    received_at: datetime
    summary: str
    labels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class HealthSummary:
    component_code: str
    display_name: str
    state: str
    freshness: str
    observation_id: UUID | None
    observed_at: datetime | None
    summary: str | None
