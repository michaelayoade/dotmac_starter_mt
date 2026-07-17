"""Tenant-scoped Person service.

`Party` (`party_type == person`) + its `PartyPerson` subtype row replaced the
bare `Person` model (Task 6). This feature's request/response shape (email,
first_name, last_name) is kept mechanically unchanged this task — the service
now creates/reads/deletes both rows under the hood. Route/schema redesign
(e.g. a `/parties` endpoint that also handles organizations) is Task 7.

`Parties` handles the plain-CRUD paths (get/delete) via `CRUDManager`;
`create_person`/`list_persons`/`get_person` combine the `Party` row with its
`PartyPerson` subtype row into a `PersonRecord` for the (unchanged)
`PersonRead` schema. All `select()`/session-mutation calls for the person
domain live here — `app/features/persons/router.py` only resolves
dependencies, calls these functions, and shapes the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crud import CRUDManager
from app.core.exceptions import ConflictError, NotFoundError
from app.core.models import Party, PartyPerson, PartyType, Tenant
from app.features.persons.schemas import PersonCreate


class Parties(CRUDManager[Party]):
    model = Party
    not_found_detail = "Person not found"


@dataclass(frozen=True)
class PersonRecord:
    """`PersonRead`-shaped combination of `Party` + its `PartyPerson` row."""

    id: UUID
    email: str | None
    first_name: str
    last_name: str


def _to_record(party: Party, subtype: PartyPerson) -> PersonRecord:
    return PersonRecord(
        id=party.id,
        email=party.email,
        first_name=subtype.first_name,
        last_name=subtype.last_name,
    )


def list_persons(db: Session) -> list[PersonRecord]:
    # No explicit tenant filter — RLS does it. If RLS were misconfigured this would
    # leak; the cross-tenant test catches that.
    stmt = (
        select(Party, PartyPerson)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .where(Party.party_type == PartyType.person)
        .order_by(Party.created_at.desc())
    )
    return [_to_record(party, subtype) for party, subtype in db.execute(stmt).all()]


def create_person(db: Session, tenant: Tenant, payload: PersonCreate) -> PersonRecord:
    party = Party(
        tenant_id=tenant.id,  # never from payload — always from request state
        party_type=PartyType.person,
        display_name=f"{payload.first_name} {payload.last_name}",
        email=payload.email,
    )
    db.add(party)
    try:
        db.flush()
        subtype = PartyPerson(
            party_id=party.id,
            first_name=payload.first_name,
            last_name=payload.last_name,
        )
        db.add(subtype)
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Email already registered") from exc
    return _to_record(party, subtype)


def get_person(db: Session, person_id: UUID) -> PersonRecord:
    party = Parties.get(db, str(person_id))
    subtype = db.get(PartyPerson, party.id)
    if subtype is None:
        raise NotFoundError(Parties.not_found_detail)
    return _to_record(party, subtype)


def delete_person(db: Session, person_id: UUID) -> None:
    party = Parties.get(db, str(person_id))
    if party.party_type != PartyType.person:
        # Org-type parties aren't reachable via /people — treat as not-found,
        # same as get_person's missing-subtype behavior. Deleting them is
        # explicitly out of scope for this endpoint.
        raise NotFoundError(Parties.not_found_detail)
    Parties.delete(db, str(person_id), commit=False)
