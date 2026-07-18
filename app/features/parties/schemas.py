"""Pydantic schemas for the tenant-scoped parties feature.

`Party` (`party_type` person|organization) replaced the bare `Person` model
(Task 6); this task (7) gives it its own API shape — `/parties/people` and
`/parties/organizations` create paths, `PartyRead` flattens both subtypes'
profile fields onto one response shape so `GET /parties` (and `GET
/parties/{id}`) can return either type uniformly.

`PersonPartyUpdate`/`OrganizationPartyUpdate` (Task 5) back the party edit
flows: no `party_type` field on either (party_type is immutable — a person
party can never become an organization, enforced in `service.py` by raising
`NotFoundError` on a type mismatch, the same "wrong type looks like missing"
convention `delete_party` already uses). Every field is optional so callers
can `model_dump(exclude_unset=True)` and only touch what was actually sent —
but `first_name`/`last_name`/`legal_name` are NOT NULLABLE columns
(`PartyPerson`/`PartyOrganization`, see `app/core/models.py`), so
`service.py`'s update functions reject an explicit `null` for those three
the same way `custom_fields/router.py`'s `NOT_NULLABLE_FIELDS` guard rejects
one for its own non-nullable columns — the schema alone can't express
"optional to omit, but never null", so the guard lives one layer down.
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


class PersonPartyUpdate(BaseModel):
    email: EmailStr | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)


class OrganizationPartyUpdate(BaseModel):
    email: EmailStr | None = None
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)


class PartyRead(BaseModel):
    id: UUID
    party_type: PartyType
    # Recomputed on every write (create AND update) by the shared
    # `app.core.identity` helpers — parties/auth services are the single
    # write-owner of this projection now (Task 5); see docs/ARCHITECTURE.md's
    # "Known dual-writer: Parties" section.
    display_name: str
    # Party emails are nullable (organizations may have none); the
    # person-create path requires EmailStr, enforced by PersonPartyCreate.
    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None
    legal_name: str | None = None
    model_config = {"from_attributes": True}
