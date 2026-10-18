"""Concrete commands and facts for the caller-owned learning-event ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class EventWrite(BaseModel):
    """One local ledger observation; this command makes no delivery claim."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    person_id: UUID
    kind: str
    course_id: UUID | None = None
    subject_id: UUID | None = None
    detail: JsonValue = Field(default_factory=dict)
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EventFact:
    """An appended event in the caller's still-uncommitted transaction."""

    id: UUID
    tenant_id: UUID
    person_id: UUID
    kind: str
    course_id: UUID | None
    subject_id: UUID | None
    detail: JsonValue
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EventEmission:
    """Optional append outcome, without exception text or delivery assertions."""

    status: Literal["recorded", "suppressed", "failed"]
    event: EventFact | None = None


@dataclass(frozen=True, slots=True)
class WeekBucket:
    week_start: date
    count: int


__all__ = ["EventWrite", "EventFact", "EventEmission", "WeekBucket"]
