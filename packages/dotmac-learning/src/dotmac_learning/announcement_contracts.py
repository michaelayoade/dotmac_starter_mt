"""Detached announcement facts and the product-owned notification boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class CreateAnnouncementCommand(BaseModel):
    """The product renders Markdown before invoking the domain command."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID
    author_person_id: UUID
    title: str
    body_md: str
    body_html: str
    cohort_id: UUID | None = None


class DeleteAnnouncementCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID
    announcement_id: UUID


@dataclass(frozen=True, slots=True)
class AnnouncementFact:
    id: UUID
    tenant_id: UUID
    cohort_id: UUID | None
    author_person_id: UUID
    title: str
    body_md: str
    body_html: str
    created_at: datetime
    updated_at: datetime


class AnnouncementFanout(Protocol):
    """Academy owns notification presentation and transport, using this Session."""

    def __call__(
        self,
        db: Session,
        announcement: AnnouncementFact,
        recipient_ids: tuple[UUID, ...],
    ) -> None: ...


__all__ = [
    "AnnouncementFact",
    "AnnouncementFanout",
    "CreateAnnouncementCommand",
    "DeleteAnnouncementCommand",
]
