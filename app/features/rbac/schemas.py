"""Pydantic schemas for the tenant-scoped RBAC and audit feature.

Moved here from `router.py` (inline models) so `service.py` can take concrete
payload types instead of `Any`. Field names, validation, and response shapes
are unchanged from the inline versions.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=120)


class RoleRead(BaseModel):
    id: UUID
    slug: str
    name: str
    model_config = {"from_attributes": True}


class RoleGrantRequest(BaseModel):
    person_id: UUID
    role_id: UUID


class AuditEventRead(BaseModel):
    id: UUID
    actor_person_id: UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict[str, object]
    created_at: datetime
    model_config = {"from_attributes": True}
