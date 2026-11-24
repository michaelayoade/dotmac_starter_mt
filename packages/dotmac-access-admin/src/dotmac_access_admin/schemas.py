"""Request and response shapes. No field can carry credential material."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserCreate(_Strict):
    # EmailStr needs the optional email-validator extra; a bounded string is
    # normalized by the kernel's `normalize_email` and checked for shape below.
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)


class UserRead(BaseModel):
    id: UUID
    email: str | None
    display_name: str
    first_name: str
    last_name: str
    is_active: bool
    roles: list[str]


class UserCreated(BaseModel):
    user: UserRead
    recovery_intent_ref: str


class RoleCreate(_Strict):
    slug: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=120)


class RoleRename(_Strict):
    name: str = Field(min_length=1, max_length=120)


class RoleRead(BaseModel):
    slug: str
    name: str
    id: UUID | None
    kind: str
    read_only: bool
    permission_codes: list[str]
    holders: int


class CompositionRequest(_Strict):
    permission_codes: list[str] = Field(max_length=500)


class CompositionRead(BaseModel):
    permission_codes: list[str]
    persisted: bool = False


class GrantRequest(_Strict):
    party_id: UUID
    role_id: UUID


class SodWarningRead(BaseModel):
    roles: list[str]
    message: str


class GrantResult(BaseModel):
    party_id: UUID
    role_id: UUID
    role_slug: str
    warnings: list[SodWarningRead]


class RevokeResult(BaseModel):
    party_id: UUID
    grants_revoked: int
    sessions_revoked: int
    deactivated: bool
