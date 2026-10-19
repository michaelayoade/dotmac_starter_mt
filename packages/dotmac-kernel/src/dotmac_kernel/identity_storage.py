"""Canonical identity storage; observations never prove authority.

Engine-free primitives extracted from Academy's canonical helper. Callers own
Session/root, business policy, authorization, Party-first lock order and
commit/rollback. Credential membership exposes IDs only; this module never
writes credentials or password material. No product profile is owned here.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from dotmac_kernel._identity_persistence import (
    _clean_boundary,
    _revoke_sessions,
    _storage_phase,
    _uuid,
)
from dotmac_kernel.models import (
    Party,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
    UserCredential,
)

__all__ = (
    "NewTenantValues",
    "TenantFact",
    "create_tenant",
    "PartyFact",
    "PersonFact",
    "RoleFact",
    "GrantFact",
    "NewPersonValues",
    "find_party_exact_email",
    "find_party_normalized_email",
    "lock_parties",
    "lock_person",
    "lock_roles",
    "lock_grants",
    "credential_member_ids",
    "create_person",
    "provision_missing_roles",
    "ensure_grant",
    "delete_person",
    "remove_grants",
    "revoke_party_sessions",
)


@dataclass(frozen=True, slots=True)
class PartyFact:
    id: UUID
    tenant_id: UUID
    party_type: PartyType
    email: str | None
    display_name: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class PersonFact:
    party_id: UUID
    first_name: str
    last_name: str


@dataclass(frozen=True, slots=True)
class RoleFact:
    id: UUID
    tenant_id: UUID
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class GrantFact:
    id: UUID
    tenant_id: UUID
    party_id: UUID
    role_id: UUID


@dataclass(frozen=True, slots=True)
class NewPersonValues:
    """Caller-decided values, with no normalization or business defaults."""

    id: UUID
    tenant_id: UUID
    email: str | None
    display_name: str
    first_name: str
    last_name: str
    is_active: bool
    custom_fields: dict[str, object]
    created_at: datetime
    updated_at: datetime


def _party(row: Party) -> PartyFact:
    return PartyFact(
        row.id,
        row.tenant_id,
        row.party_type,
        row.email,
        row.display_name,
        row.is_active,
    )


def _role(row: Role) -> RoleFact:
    return RoleFact(row.id, row.tenant_id, row.slug, row.name)


def _grant(row: PartyRoleGrant) -> GrantFact:
    return GrantFact(row.id, row.tenant_id, row.party_id, row.role_id)


def find_party_exact_email(
    db: Session, *, tenant_id: UUID, email: str
) -> PartyFact | None:
    """Admissions spelling: exact equality, no fallback or person eligibility claim."""
    row = db.scalars(
        select(Party)
        .where(Party.tenant_id == tenant_id, Party.email == email)
        .execution_options(populate_existing=True)
    ).first()
    return None if row is None else _party(row)


def find_party_normalized_email(
    db: Session, *, tenant_id: UUID, normalized_email: str
) -> PartyFact | None:
    """Human/web comparison; caller supplies its already normalized value."""
    row = db.scalars(
        select(Party)
        .where(
            Party.tenant_id == tenant_id,
            func.lower(func.btrim(Party.email)) == normalized_email,
        )
        .execution_options(populate_existing=True)
    ).first()
    return None if row is None else _party(row)


def lock_parties(
    db: Session, *, tenant_id: UUID, party_ids: tuple[UUID, ...]
) -> tuple[PartyFact, ...]:
    """Lock existing principals in UUID order; missing IDs remain visibly absent.

    Owner compares requested/resolved IDs and refuses drift. Never discover and
    lock a replacement out of order. A newly created local Party is caller-owned.
    """
    rows = []
    for party_id in sorted(set(party_ids)):
        row = db.scalars(
            select(Party)
            .where(Party.tenant_id == tenant_id, Party.id == party_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if row is not None:
            rows.append(_party(row))
    return tuple(rows)


def lock_person(db: Session, *, tenant_id: UUID, party_id: UUID) -> PersonFact | None:
    row = db.scalars(
        select(PartyPerson)
        .join(Party, Party.id == PartyPerson.party_id)
        .where(Party.tenant_id == tenant_id, PartyPerson.party_id == party_id)
        .with_for_update(of=PartyPerson)
        .execution_options(populate_existing=True)
    ).first()
    return (
        None if row is None else PersonFact(row.party_id, row.first_name, row.last_name)
    )


def lock_roles(db: Session, *, tenant_id: UUID) -> tuple[RoleFact, ...]:
    return tuple(
        _role(row)
        for row in db.scalars(
            select(Role)
            .where(Role.tenant_id == tenant_id)
            .order_by(Role.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )


def lock_grants(
    db: Session, *, tenant_id: UUID, party_ids: tuple[UUID, ...]
) -> tuple[GrantFact, ...]:
    """Caller must already hold all participating Party locks."""
    return tuple(
        _grant(row)
        for row in db.scalars(
            select(PartyRoleGrant)
            .where(
                PartyRoleGrant.tenant_id == tenant_id,
                PartyRoleGrant.party_id.in_(party_ids),
            )
            .order_by(
                PartyRoleGrant.party_id, PartyRoleGrant.role_id, PartyRoleGrant.id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )


def credential_member_ids(
    db: Session, *, tenant_id: UUID, party_id: UUID
) -> tuple[UUID, ...]:
    """Complete account membership, regardless of alias/policy/status.

    Nonempty means ANY credential exists. Caller must hold Party first, including
    when this returns empty; all credential membership writers must participate.
    Reads only IDs, never credential material, and supplies no issuance authority.
    """
    return tuple(
        db.scalars(
            select(UserCredential.id)
            .where(
                UserCredential.tenant_id == tenant_id,
                UserCredential.party_id == party_id,
            )
            .order_by(UserCredential.id)
            .with_for_update()
        ).all()
    )


def create_person(db: Session, *, values: NewPersonValues) -> Party:
    """Create canonical rows with two flushes, returning the same-Session Party.

    The strong row reference lets product wrappers append their own flushes and
    then project final observations without an extra query. It proves no authority;
    caller owns authorization, collision handling, locks and rollback.
    """
    party = Party(
        id=values.id,
        tenant_id=values.tenant_id,
        party_type=PartyType.person,
        email=values.email,
        display_name=values.display_name,
        is_active=values.is_active,
        custom_fields=deepcopy(values.custom_fields),
        created_at=values.created_at,
        updated_at=values.updated_at,
    )
    db.add(party)
    db.flush()
    db.add(
        PartyPerson(
            party_id=values.id, first_name=values.first_name, last_name=values.last_name
        )
    )
    db.flush()
    return party


def provision_missing_roles(
    db: Session,
    *,
    tenant_id: UUID,
    catalog: tuple[RoleFact, ...],
    configured_names: Mapping[str, str],
    missing_role_ids: Mapping[str, UUID],
    created_at: datetime,
    updated_at: datetime,
) -> tuple[RoleFact, ...]:
    """Extend caller's freshly locked catalog; preserve existing names/custom rows.

    Catalog facts are observations, not proof. Owner supplies the same transaction
    catalog after its required locks and validated Settings. Races propagate.
    """
    if any(row.tenant_id != tenant_id for row in catalog):
        raise ValueError("role catalog tenant differs")
    roles = {row.slug: row for row in catalog}
    if len(roles) != len(catalog):
        raise ValueError("role catalog contains duplicate slugs")
    for slug, name in sorted(configured_names.items()):
        if slug not in roles:
            row = Role(
                id=missing_role_ids[slug],
                tenant_id=tenant_id,
                slug=slug,
                name=name,
                created_at=created_at,
                updated_at=updated_at,
            )
            db.add(row)
            db.flush()
            roles[slug] = _role(row)
    return tuple(roles.values())


def ensure_grant(
    db: Session,
    *,
    tenant_id: UUID,
    party_id: UUID,
    role_id: UUID,
    new_grant_id: UUID | None,
    created_at: datetime | None,
    updated_at: datetime | None,
) -> GrantFact:
    """Add only if absent, under caller-held Party lock; no role admission decision.

    Required explicit None inputs omit those constructor fields, preserving the
    Kernel model's UUID/server timestamp defaults rather than supplying NULL.
    """
    row = db.scalars(
        select(PartyRoleGrant)
        .where(
            PartyRoleGrant.tenant_id == tenant_id,
            PartyRoleGrant.party_id == party_id,
            PartyRoleGrant.role_id == role_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        supplied: dict[str, UUID | datetime] = {}
        if new_grant_id is not None:
            supplied["id"] = new_grant_id
        if created_at is not None:
            supplied["created_at"] = created_at
        if updated_at is not None:
            supplied["updated_at"] = updated_at
        row = PartyRoleGrant(
            tenant_id=tenant_id, party_id=party_id, role_id=role_id, **supplied
        )
        db.add(row)
        db.flush()
    return _grant(row)


def delete_person(db: Session, *, tenant_id: UUID, party_id: UUID) -> None:
    """Delete the bound Party only; caller owns locks, audit, proof and rollback.

    Incoming owner FKs perform their own retention/cascade policy. In particular
    this primitive cannot qualify populated external-binding/session ordering.
    """
    from sqlalchemy import delete

    result = None
    try:
        # A DELETE without RETURNING produces a zero-column CursorResult.
        result = cast(
            CursorResult[tuple[()]],
            db.execute(
                delete(Party).where(
                    Party.tenant_id == tenant_id,
                    Party.id == party_id,
                    Party.party_type == PartyType.person,
                )
            ),
        )
        if result.rowcount != 1:
            raise ValueError("bound person deletion outcome differs")
        db.flush()
    finally:
        del db, tenant_id, party_id
        result = None


@dataclass(frozen=True, slots=True)
class NewTenantValues:
    id: UUID
    slug: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TenantFact:
    id: UUID
    slug: str
    name: str
    is_active: bool


def create_tenant(db: Session, *, values: NewTenantValues) -> TenantFact:
    """Canonical constructor only; caller owns actual offline bootstrap authority."""
    row = Tenant(
        id=values.id,
        slug=values.slug,
        name=values.name,
        is_active=values.is_active,
        created_at=values.created_at,
        updated_at=values.updated_at,
    )
    db.add(row)
    db.flush()
    return TenantFact(row.id, row.slug, row.name, row.is_active)


def remove_grants(
    db: Session,
    *,
    tenant_id: UUID,
    party_id: UUID,
    expected: tuple[GrantFact, ...],
) -> tuple[GrantFact, ...]:
    """Remove exactly fresh caller-inventoried grants; facts are not authority.

    Caller owns full Party/subordinate lock closure and an already materialized
    public conflict SAVEPOINT in its active root. No ORM state is refreshed,
    flushed, expired or populated. Any pending caller work is refused before IO.
    """
    root, savepoint = _clean_boundary(db)
    _uuid(tenant_id)
    _uuid(party_id)
    if not isinstance(expected, tuple):
        raise ValueError("identity storage grant cohort shape differs")
    for fact in expected:
        if not isinstance(fact, GrantFact):
            raise ValueError("identity storage grant cohort shape differs")
        for value in (fact.id, fact.tenant_id, fact.party_id, fact.role_id):
            _uuid(value)
        if fact.tenant_id != tenant_id or fact.party_id != party_id:
            raise ValueError("identity storage grant cohort differs")
    ids = tuple(fact.id for fact in expected)
    if len(set(ids)) != len(ids):
        raise ValueError("identity storage grant cohort shape differs")
    if not expected:
        return ()
    expected = tuple(sorted(expected, key=lambda fact: fact.id))
    table = PartyRoleGrant.__table__
    columns = (table.c.id, table.c.tenant_id, table.c.party_id, table.c.role_id)
    predicates = (
        table.c.tenant_id == tenant_id,
        table.c.party_id == party_id,
        table.c.id.in_(ids),
    )
    with _storage_phase(db):
        _clean_boundary(db, root=root, savepoint=savepoint)
        found = tuple(
            GrantFact(*row)
            for row in db.execute(
                select(*columns).where(*predicates).order_by(table.c.id)
            )
        )
        _clean_boundary(db, root=root, savepoint=savepoint)
        if found != expected:
            raise ValueError("identity storage grant cohort differs")
        removed = tuple(
            sorted(
                (
                    GrantFact(*row)
                    for row in db.execute(
                        delete(table).where(*predicates).returning(*columns)
                    )
                ),
                key=lambda fact: fact.id,
            )
        )
        _clean_boundary(db, root=root, savepoint=savepoint)
        if removed != expected:
            raise ValueError("identity storage grant removal outcome differs")
    return removed


def revoke_party_sessions(
    db: Session,
    *,
    tenant_id: UUID,
    party_id: UUID,
    session_ids: tuple[UUID, ...] | None,
    revoked_at: datetime,
) -> tuple[UUID, ...]:
    """End only the selected canonical cohort in the caller's SAVEPOINT.

    None explicitly selects all tenant/Party sessions, including expired and
    externally derived rows. A tuple selects exact membership; empty means none.
    Caller supplies the aware operation timestamp after its complete Party-first
    lock phase. IDs and returned observations confer no authority.
    """
    return _revoke_sessions(
        db,
        tenant_id=tenant_id,
        party_id=party_id,
        session_ids=session_ids,
        revoked_at=revoked_at,
    )
