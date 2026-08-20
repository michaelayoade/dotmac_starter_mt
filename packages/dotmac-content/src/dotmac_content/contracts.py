"""Typed commands, immutable values, and refusals for editorial content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from dotmac_content.lifecycle import ContentItemState, ContentPlanStatus


class ContentError(Exception):
    """Base for content-owner refusals."""


class NotFound(ContentError):
    """A referenced row does not exist in the declared tenant scope."""


class Conflict(ContentError):
    """A tenant-scoped content identity conflicts."""


@dataclass(frozen=True, slots=True)
class CreateContentPlan:
    name: str
    created_by_ref: UUID
    description: str | None = None
    starts_on: date | None = None
    ends_on: date | None = None


@dataclass(frozen=True, slots=True)
class UpdateContentPlan:
    name: str
    description: str | None
    status: ContentPlanStatus
    starts_on: date | None
    ends_on: date | None


@dataclass(frozen=True, slots=True)
class CreateContentItem:
    content_plan_id: UUID
    title: str
    body: str
    created_by_ref: UUID
    planned_for: datetime | None = None


@dataclass(frozen=True, slots=True)
class UpdateContentItem:
    title: str
    body: str
    state: ContentItemState
    planned_for: datetime | None


@dataclass(frozen=True, slots=True)
class CreateContentVariant:
    content_item_id: UUID
    variant_key: str
    title_override: str | None
    body_override: str | None
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class AttachPlanCreative:
    content_plan_id: UUID
    file_ref: UUID
    role: str
    caption: str | None
    alt_text: str | None
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class AttachItemCreative:
    content_item_id: UUID
    file_ref: UUID
    role: str
    caption: str | None
    alt_text: str | None
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class ContentVariantSnapshotV1:
    variant_id: UUID
    variant_key: str
    title_override: str | None
    body_override: str | None
    sort_order: int


@dataclass(frozen=True, slots=True)
class ContentCreativeReferenceV1:
    creative_id: UUID
    file_ref: UUID
    role: str
    caption: str | None
    alt_text: str | None
    sort_order: int


@dataclass(frozen=True, slots=True)
class ContentSnapshotV1:
    content_item_id: UUID
    content_plan_id: UUID
    title: str
    body: str
    state: ContentItemState
    planned_for: datetime | None
    created_by_ref: UUID
    variants: tuple[ContentVariantSnapshotV1, ...]
    plan_creatives: tuple[ContentCreativeReferenceV1, ...]
    item_creatives: tuple[ContentCreativeReferenceV1, ...]
    schema_version: int = 1


__all__ = [
    "AttachItemCreative",
    "AttachPlanCreative",
    "Conflict",
    "ContentCreativeReferenceV1",
    "ContentError",
    "ContentSnapshotV1",
    "ContentVariantSnapshotV1",
    "CreateContentItem",
    "CreateContentPlan",
    "CreateContentVariant",
    "NotFound",
    "UpdateContentItem",
    "UpdateContentPlan",
]
