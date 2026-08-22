"""Workforce commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class WorkforceError(Exception):
    """Base workforce refusal."""


class Conflict(WorkforceError):
    """The requested workforce mutation is inadmissible."""


@dataclass(frozen=True, slots=True)
class CreateTeam:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateSkill:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class AddTeamMember:
    team_id: UUID
    worker_reference: str
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class CertifyWorkerSkill:
    worker_reference: str
    skill_id: UUID
    proficiency: int
    verified_at: datetime


@dataclass(frozen=True, slots=True)
class CreateShift:
    team_id: UUID
    starts_at: datetime
    ends_at: datetime
    capacity: int


@dataclass(frozen=True, slots=True)
class RecordAvailability:
    worker_reference: str
    starts_at: datetime
    ends_at: datetime
    available: bool
    source_reference: str


@dataclass(frozen=True, slots=True)
class DispatchWork:
    work_reference: str
    team_id: UUID
    worker_reference: str
    required_skill_id: UUID
    scheduled_for: datetime
    decided_at: datetime
    shift_id: UUID | None = None


__all__ = [
    "AddTeamMember",
    "CertifyWorkerSkill",
    "Conflict",
    "CreateShift",
    "CreateSkill",
    "CreateTeam",
    "DispatchWork",
    "RecordAvailability",
    "WorkforceError",
]
