"""Pydantic schemas for the settings-admin JSON API (a secret carries no value)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from dotmac_settings_admin.service import MAX_REASON_LENGTH


class SettingOut(BaseModel):
    domain: str
    key: str
    # None for a secret: it is reported only as set or unset, never its value.
    value: Any | None
    value_type: str
    label: str | None
    description: str | None
    is_secret: bool
    is_set: bool
    source: str
    has_override: bool
    owner: str | None


class DomainOut(BaseModel):
    domain: str
    owner: str | None
    count: int


class SettingUpdate(BaseModel):
    value: Any
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)


class ReasonOnly(BaseModel):
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)


class WriteResult(BaseModel):
    applied: bool
    proposal_id: str | None
    setting: SettingOut


class HistoryOut(BaseModel):
    id: UUID
    action: str
    changed_at: datetime
    changed_by_party_id: UUID | None
    reason: str | None
    value_before: str | None
    value_after: str | None
    secret_changed: bool
    restorable: bool


class ImportRequest(BaseModel):
    document: dict[str, Any]


class ImportApply(ImportRequest):
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)
    digest: str


class PlanEntryOut(BaseModel):
    domain: str
    key: str
    status: str
    detail: str
    current: Any | None
    new: Any | None


class PlanOut(BaseModel):
    ok: bool
    digest: str
    entries: list[PlanEntryOut]
