"""Tenant-scoped Parties service.

`Party` (`party_type` person|organization) is the identity source of truth
(Task 6); this feature owns its own API shape (Task 7) — `create_person_party`
and `create_organization_party` create a `Party` + its subtype row
(`PartyPerson`/`PartyOrganization`) atomically via a single flush each
(`get_db` owns the commit — see `app.core.db.get_db`), `list_parties` filters
by `party_type` and paginates, and `Parties` (a `CRUDManager[Party]`) handles
plain get/delete. All `select()`/session-mutation calls for the party domain
live here — `app/features/parties/router.py` only resolves dependencies,
calls these functions, and shapes the response.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.crud import CRUDManager
from app.core.exceptions import ConflictError
from app.core.models import Party, PartyOrganization, PartyPerson, PartyType, Tenant
from app.core.query import apply_pagination
from app.features.parties.schemas import OrganizationPartyCreate, PersonPartyCreate


class Parties(CRUDManager[Party]):
    model = Party
    not_found_detail = "Party not found"


def create_person_party(
    db: Session, tenant: Tenant, payload: PersonPartyCreate
) -> Party:
    email = payload.email.lower()
    party = Party(
        tenant_id=tenant.id,  # never from payload — always from request state
        party_type=PartyType.person,
        # write-once until an update endpoint exists (see backlog)
        display_name=f"{payload.first_name} {payload.last_name}",
        email=email,
    )
    # Assigning via the relationship (rather than setting party_id by hand)
    # lets SQLAlchemy's cascade add both rows and resolve the FK in a single
    # flush, keeping the in-memory object graph populated for the caller
    # without a second round trip.
    party.person_profile = PartyPerson(
        first_name=payload.first_name, last_name=payload.last_name
    )
    db.add(party)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Email already registered") from exc
    return party


def create_organization_party(
    db: Session, tenant: Tenant, payload: OrganizationPartyCreate
) -> Party:
    email = payload.email.lower() if payload.email else None
    party = Party(
        tenant_id=tenant.id,  # never from payload — always from request state
        party_type=PartyType.organization,
        # write-once until an update endpoint exists (see backlog)
        display_name=payload.legal_name,
        email=email,
    )
    party.organization_profile = PartyOrganization(legal_name=payload.legal_name)
    db.add(party)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Email already registered") from exc
    return party


def list_parties(
    db: Session, *, party_type: PartyType | None, limit: int, offset: int
) -> list[Party]:
    # No explicit tenant filter — RLS does it. If RLS were misconfigured this
    # would leak; the cross-tenant test catches that.
    stmt = select(Party).options(
        joinedload(Party.person_profile), joinedload(Party.organization_profile)
    )
    if party_type is not None:
        stmt = stmt.where(Party.party_type == party_type)
    stmt = stmt.order_by(Party.created_at.desc())
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    return list(db.scalars(stmt).unique().all())


def get_party(db: Session, party_id: UUID) -> Party:
    party = Parties.get(db, str(party_id))
    # Touch the subtype relationships now, while the session is open, so the
    # router's read-model mapping (app.features.parties.router) never issues
    # a lazy-loading query itself.
    _ = party.person_profile
    _ = party.organization_profile
    return party


def delete_party(db: Session, party_id: UUID) -> None:
    Parties.delete(db, str(party_id), commit=False)


__all__ = [
    "Parties",
    "create_organization_party",
    "create_person_party",
    "delete_party",
    "get_party",
    "list_parties",
]
