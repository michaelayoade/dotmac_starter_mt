"""Pydantic schemas for the platform-admin tenants feature.

Moved here from `router.py` (inline models) so `service.py` can take concrete
payload types instead of `Any`. Field names, validation, and response shapes
are unchanged from the inline versions.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=120)


class TenantRead(BaseModel):
    id: UUID
    slug: str
    name: str
    is_active: bool
    model_config = {"from_attributes": True}
