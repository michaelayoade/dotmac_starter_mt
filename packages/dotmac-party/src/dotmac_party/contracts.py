"""Typed commands, lifecycle states, and refusals for ``dotmac-party``."""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


class PartyModuleError(Exception):
    """Base refusal raised by the Party context owner."""


class PartyNotFound(PartyModuleError):
    """A referenced Party context row does not exist in the tenant."""


class PartyConflict(PartyModuleError):
    """A declared tenant identity already belongs to another row."""


class PartyInvariantError(PartyModuleError):
    """A requested Party fact violates the archetype or its lifecycle."""


class RoleStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ENDED = "ended"


class RelationshipStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ENDED = "ended"


class MembershipStatus(enum.StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ENDED = "ended"


class ContactVerificationStatus(enum.StrEnum):
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class ContactConsentStatus(enum.StrEnum):
    UNKNOWN = "unknown"
    OPTED_IN = "opted_in"
    OPTED_OUT = "opted_out"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class AssignPartyRole:
    party_id: UUID
    role_type: str
    role_key: str = "default"
    status: RoleStatus = RoleStatus.PENDING
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RelatePartyRoles:
    subject_role_id: UUID
    object_role_id: UUID
    relationship_type: str
    relationship_key: str = "default"
    status: RelationshipStatus = RelationshipStatus.ACTIVE
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CreatePartyMembership:
    person_party_id: UUID
    organization_party_id: UUID
    membership_type: str
    membership_key: str = "default"
    status: MembershipStatus = MembershipStatus.INVITED
    access_scope: Mapping[str, object] = field(default_factory=dict)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AddContactPoint:
    party_id: UUID
    channel_type: str
    value: str
    display_value: str | None = None
    scope_key: str = "default"
    provider: str | None = None
    provider_account_id: str | None = None
    external_subject_id: str | None = None
    is_primary: bool = False
    source: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecordExternalReference:
    party_id: UUID
    source_system: str
    entity_type: str
    external_id: str
    source: str
    metadata: Mapping[str, object] = field(default_factory=dict)


__all__ = [
    "AddContactPoint",
    "AssignPartyRole",
    "ContactConsentStatus",
    "ContactVerificationStatus",
    "CreatePartyMembership",
    "MembershipStatus",
    "PartyConflict",
    "PartyInvariantError",
    "PartyModuleError",
    "PartyNotFound",
    "RecordExternalReference",
    "RelatePartyRoles",
    "RelationshipStatus",
    "RoleStatus",
]
