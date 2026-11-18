"""Request/response contracts for the Template Studio JSON API.

Typed end to end — no `payload: Any` reaches a service function (hard rule 7,
`tests/architecture/test_service_typing.py`).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dotmac_template_studio.rendering import MAX_TEMPLATE_CHARS

_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$"
_CHANNEL_PATTERN = r"^[a-z][a-z0-9_]{0,19}$"


class TemplateCreate(BaseModel):
    slug: str = Field(pattern=_SLUG_PATTERN)
    channel: str = Field(
        pattern=_CHANNEL_PATTERN,
        description="Delivery channel — part of the identity, e.g. `email`, `sms`.",
    )
    context: str = Field(
        min_length=1,
        max_length=60,
        description=(
            "Name of a registered `RenderContext`. Its variables are the only "
            "placeholders this template's versions may use."
        ),
    )
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class TemplateUpdate(BaseModel):
    """Metadata only. `slug`, `channel` and `context` are not editable — see
    `service.update_template` for why."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    channel: str
    context: str
    name: str
    description: str | None
    is_active: bool
    published_version: int | None
    created_at: datetime
    updated_at: datetime


class VersionCreate(BaseModel):
    body: str = Field(min_length=1)
    html_body: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_TEMPLATE_CHARS,
        description=(
            "Optional html part. Refused (not stripped) if it holds markup "
            "outside the allowed email subset; stored sanitized."
        ),
    )
    subject: str | None = Field(default=None, max_length=300)
    layout_slug: str | None = Field(
        default=None, pattern=_SLUG_PATTERN, description="Optional email layout slug."
    )


class VersionUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1)
    # Omitted leaves the html part alone; an explicit null removes it.
    html_body: str | None = Field(
        default=None, min_length=1, max_length=MAX_TEMPLATE_CHARS
    )
    subject: str | None = Field(default=None, max_length=300)
    # Omitted leaves the layout alone; an explicit null removes it.
    layout_slug: str | None = Field(default=None, pattern=_SLUG_PATTERN)


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    version: int
    subject: str | None
    body: str
    html_body: str | None = None
    layout_slug: str | None = None
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
    # Present only when the published revision has an html part.
    html: str | None = None


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
