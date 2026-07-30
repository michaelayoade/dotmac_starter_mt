"""Pydantic schemas for the platform-admin tenants feature.

Moved here from `router.py` (inline models) so `service.py` can take concrete
payload types instead of `Any`. Field names, validation, and response shapes
are unchanged from the inline versions.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class TenantProvision(BaseModel):
    """Atomic tenant provisioning payload (control-plane security Task 2).

    BREAKING: replaces the bare `TenantCreate {slug, name}` — a tenant
    without a login-able owner was an unusable half-state (registration is
    policy-closed by default, so nobody could ever get in). Provisioning is
    now the only owner-creation path.
    """

    slug: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=120)
    owner_email: EmailStr
    owner_password: str = Field(min_length=8, max_length=256)
    owner_first_name: str = Field(default="Owner", min_length=1, max_length=80)
    owner_last_name: str = Field(default="Account", min_length=1, max_length=80)


class TenantRead(BaseModel):
    id: UUID
    slug: str
    name: str
    is_active: bool
    model_config = {"from_attributes": True}
