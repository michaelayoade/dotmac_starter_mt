"""Pydantic schemas for the tenant-scoped parties feature.

`Party` (`party_type` person|organization) replaced the bare `Person` model
(Task 6); this task (7) gives it its own API shape — `/parties/people` and
`/parties/organizations` create paths, `PartyRead` flattens both subtypes'
profile fields onto one response shape so `GET /parties` (and `GET
/parties/{id}`) can return either type uniformly.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.core.models import PartyType


class PersonPartyCreate(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)


class OrganizationPartyCreate(BaseModel):
    # Organizations commonly have no single-address contact email — nullable,
    # unlike the person path where email is required.
    email: EmailStr | None = None
    legal_name: str = Field(min_length=1, max_length=200)


class PartyRead(BaseModel):
    id: UUID
    party_type: PartyType
    # Derived once at create time (first + " " + last for persons,
    # legal_name for organizations) — write-once until an update endpoint
    # exists (see backlog); there is no PATCH this phase.
    display_name: str
    # Party emails are nullable (organizations may have none); the
    # person-create path requires EmailStr, enforced by PersonPartyCreate.
    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None
    legal_name: str | None = None
    model_config = {"from_attributes": True}
