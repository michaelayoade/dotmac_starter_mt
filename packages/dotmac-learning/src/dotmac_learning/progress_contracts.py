"""Typed progress commands and detached domain facts; caller owns transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from .event_contracts import EventEmission


class CompletionCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID
    person_id: UUID
    course_id: UUID
    now: datetime | None = None


class CertificateCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID
    person_id: UUID
    course_id: UUID
    now: datetime | None = None


@dataclass(frozen=True)
class ScoreFact:
    id: UUID
    tenant_id: UUID
    submission_id: UUID
    score: float
    max_score: float
    fraction: float
    passed: bool
    # Existing JSONB records have no database shape check. Keep their JSON
    # losslessly, including nested values and historical scalar payloads.
    per_item: JsonValue
    source: str
    override_reason: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CompletionFact:
    id: UUID
    tenant_id: UUID
    person_id: UUID
    course_id: UUID
    status: str
    pct: float
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CertificateFact:
    id: UUID
    tenant_id: UUID
    person_id: UUID
    course_id: UUID
    serial: str
    issued_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CertificateIssue:
    certificate: CertificateFact
    created: bool
    event: EventEmission | None = None


@dataclass(frozen=True)
class CompletionResult:
    completion: CompletionFact
    first_completion: bool
    certificate: CertificateIssue | None = None
