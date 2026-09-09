"""Tenant Party context decisions extracted product-first from Sub.

Every command requires an explicit :class:`~dotmac_kernel.cache.TenantScope`,
mutates inside the caller's transaction, and flushes without commit or rollback.
The module never authenticates a Party, grants a permission, owns an Account,
or infers identity from a contact value.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Party, PartyType
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_party.contracts import (
    AddContactPoint,
    AssignPartyRole,
    ContactConsentStatus,
    ContactVerificationStatus,
    CreatePartyMembership,
    MembershipStatus,
    PartyConflict,
    PartyInvariantError,
    PartyNotFound,
    RecordExternalReference,
    RelatePartyRoles,
    RelationshipStatus,
    RoleStatus,
)
from dotmac_party.models import (
    PartyContactPoint,
    PartyExternalReference,
    PartyMembership,
    PartyRelationship,
    PartyRole,
)
from dotmac_party.vocabulary import (
    PartyVocabularyError,
    PartyVocabularyRegistry,
)

_Model = TypeVar("_Model")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-party requires an explicit TenantScope")
    return scope.tenant_id


def _required(value: str | None, field: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise PartyInvariantError(f"{field} must not be empty")
    return normalized


def _window(valid_from: datetime | None, valid_until: datetime | None) -> None:
    if valid_from is not None and valid_until is not None and valid_until <= valid_from:
        raise PartyInvariantError("valid_until must be after valid_from")


def _declared(load: Callable[[], _Model]) -> _Model:
    try:
        return load()
    except PartyVocabularyError as exc:
        raise PartyInvariantError(str(exc)) from exc


def _one(statement: Select[tuple[_Model]], db: Session, detail: str) -> _Model:
    row = db.scalar(statement)
    if row is None:
        raise PartyNotFound(detail)
    return row


def _party(db: Session, tenant_id: UUID, party_id: UUID) -> Party:
    party = _one(
        select(Party).where(Party.tenant_id == tenant_id, Party.id == party_id),
        db,
        f"Party {party_id} was not found in tenant {tenant_id}",
    )
    if not party.is_active:
        raise PartyInvariantError(f"Party {party_id} is inactive")
    return party


def _typed_party(
    db: Session, tenant_id: UUID, party_id: UUID, expected: PartyType
) -> Party:
    party = _party(db, tenant_id, party_id)
    if party.party_type != expected:
        label = "Person" if expected is PartyType.person else "Organization"
        raise PartyInvariantError(f"Party {party_id} must be a {label} Party")
    return party


def _role(db: Session, tenant_id: UUID, role_id: UUID) -> PartyRole:
    return _one(
        select(PartyRole).where(
            PartyRole.tenant_id == tenant_id, PartyRole.id == role_id
        ),
        db,
        f"PartyRole {role_id} was not found in tenant {tenant_id}",
    )


def _contact_point(
    db: Session, tenant_id: UUID, contact_point_id: UUID
) -> PartyContactPoint:
    return _one(
        select(PartyContactPoint).where(
            PartyContactPoint.tenant_id == tenant_id,
            PartyContactPoint.id == contact_point_id,
        ),
        db,
        f"PartyContactPoint {contact_point_id} was not found in tenant {tenant_id}",
    )


def _relationship(
    db: Session, tenant_id: UUID, relationship_id: UUID
) -> PartyRelationship:
    return _one(
        select(PartyRelationship).where(
            PartyRelationship.tenant_id == tenant_id,
            PartyRelationship.id == relationship_id,
        ),
        db,
        f"PartyRelationship {relationship_id} was not found in tenant {tenant_id}",
    )


def _membership(db: Session, tenant_id: UUID, membership_id: UUID) -> PartyMembership:
    return _one(
        select(PartyMembership).where(
            PartyMembership.tenant_id == tenant_id,
            PartyMembership.id == membership_id,
        ),
        db,
        f"PartyMembership {membership_id} was not found in tenant {tenant_id}",
    )


def _flush_new(db: Session, row: _Model, *, detail: str) -> _Model:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise PartyConflict(detail) from exc
    return row


def assign_role(
    db: Session,
    *,
    scope: TenantScope,
    vocabulary: PartyVocabularyRegistry,
    command: AssignPartyRole,
) -> PartyRole:
    tenant_id = _tenant(scope)
    _party(db, tenant_id, command.party_id)
    spec = _declared(lambda: vocabulary.role_type(command.role_type))
    role_key = _required(command.role_key, "role_key").lower()
    if spec.key_required and role_key == "default":
        raise PartyInvariantError(
            f"role_type {spec.code!r} requires an explicit non-default role_key"
        )
    if not spec.key_required and role_key != "default":
        raise PartyInvariantError(
            f"role_type {spec.code!r} requires role_key='default'"
        )
    _window(command.valid_from, command.valid_until)
    existing = db.scalar(
        select(PartyRole.id).where(
            PartyRole.tenant_id == tenant_id,
            PartyRole.party_id == command.party_id,
            PartyRole.role_type == spec.code,
            PartyRole.role_key == role_key,
        )
    )
    if existing is not None:
        raise PartyConflict(
            f"Party {command.party_id} already has role {spec.code}:{role_key}"
        )
    return _flush_new(
        db,
        PartyRole(
            tenant_id=tenant_id,
            party_id=command.party_id,
            role_type=spec.code,
            role_key=role_key,
            status=command.status.value,
            valid_from=command.valid_from,
            valid_until=command.valid_until,
            source=(command.source or "").strip() or None,
            metadata_=dict(command.metadata) or None,
        ),
        detail=f"Party role {spec.code}:{role_key} conflicts",
    )


_ROLE_TRANSITIONS: dict[RoleStatus, frozenset[RoleStatus]] = {
    RoleStatus.PENDING: frozenset({RoleStatus.ACTIVE, RoleStatus.ENDED}),
    RoleStatus.ACTIVE: frozenset({RoleStatus.SUSPENDED, RoleStatus.ENDED}),
    RoleStatus.SUSPENDED: frozenset({RoleStatus.ACTIVE, RoleStatus.ENDED}),
    RoleStatus.ENDED: frozenset(),
}


def transition_role(
    db: Session,
    *,
    scope: TenantScope,
    role_id: UUID,
    status: RoleStatus,
) -> PartyRole:
    tenant_id = _tenant(scope)
    role = _role(db, tenant_id, role_id)
    current = RoleStatus(role.status)
    if current is status:
        return role
    if status not in _ROLE_TRANSITIONS[current]:
        terminal = " terminal" if current is RoleStatus.ENDED else ""
        raise PartyInvariantError(
            f"PartyRole {role_id} is in{terminal} status {current.value!r}; "
            f"transition to {status.value!r} is not allowed"
        )
    role.status = status.value
    db.flush()
    return role


def relate_roles(
    db: Session,
    *,
    scope: TenantScope,
    vocabulary: PartyVocabularyRegistry,
    command: RelatePartyRoles,
) -> PartyRelationship:
    tenant_id = _tenant(scope)
    if command.subject_role_id == command.object_role_id:
        raise PartyInvariantError("a PartyRole cannot relate to itself")
    subject = _role(db, tenant_id, command.subject_role_id)
    object_ = _role(db, tenant_id, command.object_role_id)
    _party(db, tenant_id, subject.party_id)
    _party(db, tenant_id, object_.party_id)
    if (
        subject.status == RoleStatus.ENDED.value
        or object_.status == RoleStatus.ENDED.value
    ):
        raise PartyInvariantError("an ended PartyRole cannot receive a relationship")
    spec = _declared(lambda: vocabulary.relationship_type(command.relationship_type))
    relationship_key = _required(command.relationship_key, "relationship_key").lower()
    if subject.role_type not in spec.subject_role_types:
        raise PartyInvariantError(
            f"relationship type {spec.code!r} rejects subject role type "
            f"{subject.role_type!r}"
        )
    if object_.role_type not in spec.object_role_types:
        raise PartyInvariantError(
            f"relationship type {spec.code!r} rejects object role type "
            f"{object_.role_type!r}"
        )
    if spec.key_required and relationship_key == "default":
        raise PartyInvariantError(
            f"relationship type {spec.code!r} requires an explicit key"
        )
    if not spec.key_required and relationship_key != "default":
        raise PartyInvariantError(
            f"relationship type {spec.code!r} requires relationship_key='default'"
        )
    _window(command.valid_from, command.valid_until)
    existing = db.scalar(
        select(PartyRelationship.id).where(
            PartyRelationship.tenant_id == tenant_id,
            PartyRelationship.subject_role_id == subject.id,
            PartyRelationship.object_role_id == object_.id,
            PartyRelationship.relationship_type == spec.code,
            PartyRelationship.relationship_key == relationship_key,
        )
    )
    if existing is not None:
        raise PartyConflict("the exact PartyRole relationship already exists")
    return _flush_new(
        db,
        PartyRelationship(
            tenant_id=tenant_id,
            subject_role_id=subject.id,
            object_role_id=object_.id,
            relationship_type=spec.code,
            relationship_key=relationship_key,
            status=command.status.value,
            valid_from=command.valid_from,
            valid_until=command.valid_until,
            source=(command.source or "").strip() or None,
            metadata_=dict(command.metadata) or None,
        ),
        detail="PartyRole relationship conflicts",
    )


_RELATIONSHIP_TRANSITIONS: dict[RelationshipStatus, frozenset[RelationshipStatus]] = {
    RelationshipStatus.PENDING: frozenset(
        {
            RelationshipStatus.ACTIVE,
            RelationshipStatus.INACTIVE,
            RelationshipStatus.ENDED,
        }
    ),
    RelationshipStatus.ACTIVE: frozenset(
        {RelationshipStatus.INACTIVE, RelationshipStatus.ENDED}
    ),
    RelationshipStatus.INACTIVE: frozenset(
        {RelationshipStatus.ACTIVE, RelationshipStatus.ENDED}
    ),
    RelationshipStatus.ENDED: frozenset(),
}


def transition_relationship(
    db: Session,
    *,
    scope: TenantScope,
    relationship_id: UUID,
    status: RelationshipStatus,
) -> PartyRelationship:
    relationship = _relationship(db, _tenant(scope), relationship_id)
    current = RelationshipStatus(relationship.status)
    if current is status:
        return relationship
    if status not in _RELATIONSHIP_TRANSITIONS[current]:
        terminal = " terminal" if current is RelationshipStatus.ENDED else ""
        raise PartyInvariantError(
            f"PartyRelationship {relationship_id} is in{terminal} status "
            f"{current.value!r}; transition to {status.value!r} is not allowed"
        )
    relationship.status = status.value
    db.flush()
    return relationship


def create_membership(
    db: Session,
    *,
    scope: TenantScope,
    vocabulary: PartyVocabularyRegistry,
    command: CreatePartyMembership,
) -> PartyMembership:
    tenant_id = _tenant(scope)
    person = _typed_party(db, tenant_id, command.person_party_id, PartyType.person)
    organization = _typed_party(
        db, tenant_id, command.organization_party_id, PartyType.organization
    )
    spec = _declared(lambda: vocabulary.membership_type(command.membership_type))
    membership_key = _required(command.membership_key, "membership_key").lower()
    if spec.key_required and membership_key == "default":
        raise PartyInvariantError(
            f"membership type {spec.code!r} requires an explicit key"
        )
    if not spec.key_required and membership_key != "default":
        raise PartyInvariantError(
            f"membership type {spec.code!r} requires membership_key='default'"
        )
    access_scope = _declared(lambda: spec.normalize_access_scope(command.access_scope))
    _window(command.valid_from, command.valid_until)
    existing = db.scalar(
        select(PartyMembership.id).where(
            PartyMembership.tenant_id == tenant_id,
            PartyMembership.person_party_id == person.id,
            PartyMembership.organization_party_id == organization.id,
            PartyMembership.membership_type == spec.code,
            PartyMembership.membership_key == membership_key,
        )
    )
    if existing is not None:
        raise PartyConflict("the exact Party membership already exists")
    return _flush_new(
        db,
        PartyMembership(
            tenant_id=tenant_id,
            person_party_id=person.id,
            organization_party_id=organization.id,
            membership_type=spec.code,
            membership_key=membership_key,
            status=command.status.value,
            access_scope=access_scope,
            valid_from=command.valid_from,
            valid_until=command.valid_until,
            source=(command.source or "").strip() or None,
            metadata_=dict(command.metadata) or None,
        ),
        detail="Party membership conflicts",
    )


_MEMBERSHIP_TRANSITIONS: dict[MembershipStatus, frozenset[MembershipStatus]] = {
    MembershipStatus.INVITED: frozenset(
        {MembershipStatus.ACTIVE, MembershipStatus.ENDED}
    ),
    MembershipStatus.ACTIVE: frozenset(
        {MembershipStatus.SUSPENDED, MembershipStatus.ENDED}
    ),
    MembershipStatus.SUSPENDED: frozenset(
        {MembershipStatus.ACTIVE, MembershipStatus.ENDED}
    ),
    MembershipStatus.ENDED: frozenset(),
}


def transition_membership(
    db: Session,
    *,
    scope: TenantScope,
    membership_id: UUID,
    status: MembershipStatus,
) -> PartyMembership:
    membership = _membership(db, _tenant(scope), membership_id)
    current = MembershipStatus(membership.status)
    if current is status:
        return membership
    if status not in _MEMBERSHIP_TRANSITIONS[current]:
        terminal = " terminal" if current is MembershipStatus.ENDED else ""
        raise PartyInvariantError(
            f"PartyMembership {membership_id} is in{terminal} status "
            f"{current.value!r}; transition to {status.value!r} is not allowed"
        )
    membership.status = status.value
    db.flush()
    return membership


def add_contact_point(
    db: Session,
    *,
    scope: TenantScope,
    vocabulary: PartyVocabularyRegistry,
    command: AddContactPoint,
) -> PartyContactPoint:
    from dotmac_kernel.db import conflict_savepoint

    tenant_id = _tenant(scope)
    _party(db, tenant_id, command.party_id)
    spec = _declared(lambda: vocabulary.contact_channel(command.channel_type))
    try:
        normalized_value = spec.normalizer(command.value)
    except (PartyVocabularyError, ValueError) as exc:
        raise PartyInvariantError(str(exc)) from exc
    normalized_value = _required(normalized_value, "contact value")
    scope_key = _required(command.scope_key, "scope_key").lower()
    provider_values = (
        (command.provider or "").strip(),
        (command.provider_account_id or "").strip(),
        (command.external_subject_id or "").strip(),
    )
    if any(provider_values) and not all(provider_values):
        raise PartyInvariantError(
            "provider, provider_account_id, and external_subject_id must be "
            "supplied together"
        )
    if spec.requires_provider_identity and not all(provider_values):
        raise PartyInvariantError(
            f"contact channel {spec.code!r} requires provider, provider account, "
            "and immutable provider subject identity"
        )
    if db.scalar(
        select(PartyContactPoint.id).where(
            PartyContactPoint.tenant_id == tenant_id,
            PartyContactPoint.party_id == command.party_id,
            PartyContactPoint.channel_type == spec.code,
            PartyContactPoint.normalized_value == normalized_value,
            PartyContactPoint.scope_key == scope_key,
        )
    ):
        raise PartyConflict("the exact Party contact point already exists")

    current_primary: PartyContactPoint | None = None
    if command.is_primary:
        current_primary = db.scalar(
            select(PartyContactPoint)
            .where(
                PartyContactPoint.tenant_id == tenant_id,
                PartyContactPoint.party_id == command.party_id,
                PartyContactPoint.channel_type == spec.code,
                PartyContactPoint.scope_key == scope_key,
                PartyContactPoint.is_primary.is_(True),
                PartyContactPoint.is_active.is_(True),
            )
            .with_for_update()
        )

    point = PartyContactPoint(
        tenant_id=tenant_id,
        party_id=command.party_id,
        channel_type=spec.code,
        normalized_value=normalized_value,
        display_value=(command.display_value or "").strip() or command.value.strip(),
        scope_key=scope_key,
        provider=provider_values[0] or None,
        provider_account_id=provider_values[1] or None,
        external_subject_id=provider_values[2] or None,
        is_primary=command.is_primary,
        source=(command.source or "").strip() or None,
        metadata_=dict(command.metadata) or None,
    )
    try:
        with conflict_savepoint(db):
            if current_primary is not None:
                current_primary.is_primary = False
                db.flush()
            db.add(point)
            db.flush()
    except IntegrityError as exc:
        raise PartyConflict("Party contact point conflicts") from exc
    return point


def set_contact_active(
    db: Session,
    *,
    scope: TenantScope,
    contact_point_id: UUID,
    active: bool,
) -> PartyContactPoint:
    point = _contact_point(db, _tenant(scope), contact_point_id)
    if point.is_active is active:
        return point
    point.is_active = active
    if not active:
        point.is_primary = False
    db.flush()
    return point


def set_primary_contact(
    db: Session,
    *,
    scope: TenantScope,
    contact_point_id: UUID,
) -> PartyContactPoint:
    from dotmac_kernel.db import conflict_savepoint

    tenant_id = _tenant(scope)
    point = _contact_point(db, tenant_id, contact_point_id)
    if not point.is_active:
        raise PartyInvariantError("an inactive contact point cannot be primary")
    current = db.scalar(
        select(PartyContactPoint)
        .where(
            PartyContactPoint.tenant_id == tenant_id,
            PartyContactPoint.party_id == point.party_id,
            PartyContactPoint.channel_type == point.channel_type,
            PartyContactPoint.scope_key == point.scope_key,
            PartyContactPoint.is_primary.is_(True),
            PartyContactPoint.is_active.is_(True),
        )
        .with_for_update()
    )
    if current is not None and current.id == point.id:
        return point
    try:
        with conflict_savepoint(db):
            if current is not None:
                current.is_primary = False
                db.flush()
            point.is_primary = True
            db.flush()
    except IntegrityError as exc:
        raise PartyConflict("Party primary contact conflicts") from exc
    return point


def set_contact_verification(
    db: Session,
    *,
    scope: TenantScope,
    contact_point_id: UUID,
    status: ContactVerificationStatus,
    source: str,
    occurred_at: datetime,
) -> PartyContactPoint:
    point = _contact_point(db, _tenant(scope), contact_point_id)
    point.verification_status = status.value
    point.verification_source = _required(source, "verification source")
    point.verified_at = (
        occurred_at if status is ContactVerificationStatus.VERIFIED else None
    )
    db.flush()
    return point


def set_contact_consent(
    db: Session,
    *,
    scope: TenantScope,
    contact_point_id: UUID,
    status: ContactConsentStatus,
    source: str,
    occurred_at: datetime,
) -> PartyContactPoint:
    point = _contact_point(db, _tenant(scope), contact_point_id)
    point.consent_status = status.value
    point.consent_source = _required(source, "consent source")
    point.consent_captured_at = occurred_at
    db.flush()
    return point


def record_external_reference(
    db: Session,
    *,
    scope: TenantScope,
    command: RecordExternalReference,
) -> PartyExternalReference:
    tenant_id = _tenant(scope)
    _party(db, tenant_id, command.party_id)
    source_system = _required(command.source_system, "source_system").lower()
    entity_type = _required(command.entity_type, "entity_type").lower()
    external_id = _required(command.external_id, "external_id")
    source = _required(command.source, "source")
    if db.scalar(
        select(PartyExternalReference.id).where(
            PartyExternalReference.tenant_id == tenant_id,
            PartyExternalReference.source_system == source_system,
            PartyExternalReference.entity_type == entity_type,
            PartyExternalReference.external_id == external_id,
        )
    ):
        raise PartyConflict(
            "external reference already resolves to a Party in this tenant"
        )
    return _flush_new(
        db,
        PartyExternalReference(
            tenant_id=tenant_id,
            party_id=command.party_id,
            source_system=source_system,
            entity_type=entity_type,
            external_id=external_id,
            source=source,
            metadata_=dict(command.metadata) or None,
        ),
        detail="Party external reference conflicts",
    )


__all__ = [
    "add_contact_point",
    "assign_role",
    "create_membership",
    "record_external_reference",
    "relate_roles",
    "set_contact_active",
    "set_contact_consent",
    "set_primary_contact",
    "set_contact_verification",
    "transition_membership",
    "transition_relationship",
    "transition_role",
]
