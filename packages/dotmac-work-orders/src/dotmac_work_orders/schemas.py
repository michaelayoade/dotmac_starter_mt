"""Typed commands accepted by the work-order owner."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from dotmac_work_orders.lifecycle import Event, Status

NonBlank = Annotated[str, Field(min_length=1, max_length=200)]


class CreateWorkOrder(BaseModel):
    public_id: Annotated[str, Field(min_length=1, max_length=80)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: str | None = None
    status: Status = Status.SCHEDULED
    priority: Annotated[str, Field(min_length=1, max_length=20)]
    work_type: Annotated[str | None, Field(max_length=80)] = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    estimated_duration_minutes: Annotated[int | None, Field(ge=1)] = None
    address: Annotated[str | None, Field(max_length=255)] = None
    access_notes: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    minimum_photo_count: Annotated[int, Field(ge=0)] = 0
    customer_signoff_required: bool = False
    signature_unavailable_reason_allowed: bool = True
    required_evidence_kinds: list[str] = Field(default_factory=list)
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("status")
    @classmethod
    def initial_status_only(cls, value: Status) -> Status:
        if value not in {Status.DRAFT, Status.SCHEDULED}:
            raise ValueError("a work order must be created as draft or scheduled")
        return value

    @model_validator(mode="after")
    def valid_schedule(self) -> CreateWorkOrder:
        if (
            self.scheduled_start is not None
            and self.scheduled_end is not None
            and self.scheduled_end <= self.scheduled_start
        ):
            raise ValueError("scheduled_end must be after scheduled_start")
        return self


class AssignWorkOrder(BaseModel):
    assignee_id: UUID
    assignee_kind: Annotated[str, Field(min_length=1, max_length=40)]
    assigned_by_id: UUID
    client_assignment_id: UUID
    reason: str | None = None


class UnassignWorkOrder(BaseModel):
    unassigned_by_id: UUID
    client_unassignment_id: UUID
    reason: str | None = None


class UpdateWorkOrder(BaseModel):
    """Mutable header fields; lifecycle and assignment have separate commands."""

    client_command_id: UUID
    title: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    description: str | None = None
    priority: Annotated[str | None, Field(min_length=1, max_length=20)] = None
    work_type: Annotated[str | None, Field(max_length=80)] = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    estimated_duration_minutes: Annotated[int | None, Field(ge=1)] = None
    address: Annotated[str | None, Field(max_length=255)] = None
    access_notes: str | None = None
    required_skills: list[str] | None = None
    tags: list[str] | None = None
    minimum_photo_count: Annotated[int | None, Field(ge=0)] = None
    customer_signoff_required: bool | None = None
    signature_unavailable_reason_allowed: bool | None = None
    required_evidence_kinds: list[str] | None = None

    @model_validator(mode="after")
    def has_mutation(self) -> UpdateWorkOrder:
        changed = self.model_fields_set - {"client_command_id"}
        if not changed:
            raise ValueError("at least one mutable work-order field is required")
        non_nullable = {
            "title",
            "priority",
            "required_skills",
            "tags",
            "minimum_photo_count",
            "customer_signoff_required",
            "signature_unavailable_reason_allowed",
            "required_evidence_kinds",
        }
        if any(getattr(self, name) is None for name in changed & non_nullable):
            raise ValueError("required work-order fields cannot be set to null")
        if (
            self.scheduled_start is not None
            and self.scheduled_end is not None
            and self.scheduled_end <= self.scheduled_start
        ):
            raise ValueError("scheduled_end must be after scheduled_start")
        return self


class ExecutionEvent(BaseModel):
    event: Event
    client_event_id: UUID
    actor_id: UUID
    occurred_at: datetime
    latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    note: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class AddEvidence(BaseModel):
    kind: Annotated[str, Field(min_length=1, max_length=60)]
    artifact_reference: Annotated[str, Field(min_length=1, max_length=255)]
    recorded_by_id: UUID
    client_evidence_id: UUID
    captured_at: datetime | None = None
    latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    metadata: dict[str, object] = Field(default_factory=dict)


class AddNote(BaseModel):
    body: NonBlank
    author_id: UUID
    client_note_id: UUID
    internal: bool = True
    metadata: dict[str, object] = Field(default_factory=dict)


class RecordWorkLog(BaseModel):
    actor_id: UUID
    started_at: datetime
    ended_at: datetime
    client_worklog_id: UUID
    notes: str | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> RecordWorkLog:
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


__all__ = [
    "AddEvidence",
    "AddNote",
    "AssignWorkOrder",
    "CreateWorkOrder",
    "ExecutionEvent",
    "RecordWorkLog",
    "UnassignWorkOrder",
    "UpdateWorkOrder",
]
