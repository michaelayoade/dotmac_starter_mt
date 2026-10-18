"""Frozen scheduling inputs and detached facts; authorization stays in the product."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SchedulingBadRequest(ValueError):
    """Invalid scheduling operation, with the Academy source detail."""


class SchedulingNotFound(ValueError):
    """Missing tenant-scoped cohort/session or inaccessible learner session."""


class DeliveryCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID


class SetDeliveryModeCommand(DeliveryCommand):
    cohort_id: UUID
    mode: str


class CreateSessionCommand(DeliveryCommand):
    cohort_id: UUID
    title: str
    starts_at: datetime
    session_type: str
    ends_at: datetime | None = None
    offering_id: UUID | None = None
    instructor_person_id: UUID | None = None
    location: str | None = None
    join_url: str | None = None
    notes: str | None = None


class UpdateSessionCommand(DeliveryCommand):
    """Only model_fields_set are applied. Explicit None retains source behavior."""

    session_id: UUID
    title: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    session_type: str | None = None
    location: str | None = None
    join_url: str | None = None
    notes: str | None = None
    status: str | None = None


class CancelSessionCommand(DeliveryCommand):
    session_id: UUID


@dataclass(frozen=True, slots=True)
class CohortDeliveryFact:
    id: UUID
    tenant_id: UUID
    delivery_mode: str


@dataclass(frozen=True, slots=True)
class ClassSessionFact:
    id: UUID
    tenant_id: UUID
    cohort_id: UUID
    offering_id: UUID | None
    session_type: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    instructor_person_id: UUID | None
    location: str | None
    join_url: str | None
    notes: str | None
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SessionChange:
    field: str
    from_value: str | None
    to_value: str | None


@dataclass(frozen=True, slots=True)
class SessionUpdate:
    session: ClassSessionFact
    changes: tuple[SessionChange, ...]


@dataclass(frozen=True, slots=True)
class PersonSession:
    session: ClassSessionFact
    cohort_name: str


@dataclass(frozen=True, slots=True)
class PersonTimetable:
    upcoming: tuple[PersonSession, ...]
    past: tuple[PersonSession, ...]
