"""Request/response contracts for the Template Studio JSON API.

Typed end to end — no `payload: Any` reaches a service function (hard rule 7,
`tests/architecture/test_service_typing.py`).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$"


class TemplateCreate(BaseModel):
    kind: str = Field(description="`notification` or `document`")
    slug: str = Field(pattern=_SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    channel: str | None = Field(default=None, max_length=20)


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    channel: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    slug: str
    name: str
    description: str | None
    channel: str | None
    is_active: bool
    published_version: int | None
    created_at: datetime
    updated_at: datetime


class VersionCreate(BaseModel):
    body: str = Field(min_length=1)
    subject: str | None = Field(default=None, max_length=300)


class VersionUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1)
    subject: str | None = Field(default=None, max_length=300)


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    version: int
    subject: str | None
    body: str
    variables: list[str]
    author_party_id: UUID | None
    published_at: datetime | None
    created_at: datetime


class RenderRequest(BaseModel):
    # Values for the template's declared variables. A flat string map on
    # purpose: a template renders TEXT, and accepting nested structures would
    # invite callers to push formatting decisions into the substitution layer.
    values: dict[str, str] = Field(default_factory=dict)


class RenderResult(BaseModel):
    subject: str | None
    body: str


__all__ = [
    "RenderRequest",
    "RenderResult",
    "TemplateCreate",
    "TemplateRead",
    "TemplateUpdate",
    "VersionCreate",
    "VersionRead",
    "VersionUpdate",
]
