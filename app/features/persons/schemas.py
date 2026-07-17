"""Pydantic schemas for the tenant-scoped persons feature.

Moved here from `router.py` (inline models) so `service.py` can take concrete
payload types instead of `Any`. Field names, validation, and response shapes
are unchanged from the inline versions.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class PersonCreate(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)


class PersonRead(BaseModel):
    id: UUID
    email: EmailStr
    first_name: str
    last_name: str
    model_config = {"from_attributes": True}
