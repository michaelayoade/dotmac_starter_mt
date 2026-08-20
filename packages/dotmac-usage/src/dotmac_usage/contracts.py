"""Normalized usage commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


class UsageError(Exception):
    """Base normalized-usage refusal."""


class Conflict(UsageError):
    """The requested usage mutation is inadmissible."""


@dataclass(frozen=True, slots=True)
class RecordUsageObservation:
    service_reference: str
    meter_code: str
    period_start: datetime
    period_end: datetime
    quantity: Decimal
    unit: str
    source_reference: str
    source_event_id: str


@dataclass(frozen=True, slots=True)
class CorrectUsage:
    observation_id: UUID
    delta_quantity: Decimal
    reason: str
    corrected_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectUsageAggregate:
    service_reference: str
    meter_code: str
    window_start: datetime
    window_end: datetime
    quantity: Decimal
    computed_at: datetime


__all__ = [
    "Conflict",
    "CorrectUsage",
    "ProjectUsageAggregate",
    "RecordUsageObservation",
    "UsageError",
]
