"""Tenant-scoped Person service.

`Persons` handles the plain-CRUD paths (create/get/delete) via `CRUDManager`;
`list_persons` is the one query that isn't single-entity CRUD. All
`select()`/session-mutation calls for the person domain live here —
`app/features/persons/router.py` only resolves dependencies, calls these
functions, and shapes the response.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crud import CRUDManager
from app.core.exceptions import ConflictError
from app.core.models import Person, Tenant
from app.features.persons.schemas import PersonCreate


class Persons(CRUDManager[Person]):
    model = Person
    not_found_detail = "Person not found"


def list_persons(db: Session) -> list[Person]:
    # No explicit tenant filter — RLS does it. If RLS were misconfigured this would
    # leak; the cross-tenant test catches that.
    return list(db.scalars(select(Person).order_by(Person.created_at.desc())).all())


def create_person(db: Session, tenant: Tenant, payload: PersonCreate) -> Person:
    data = {
        "tenant_id": tenant.id,  # never from payload — always from request state
        "email": payload.email,
        "first_name": payload.first_name,
        "last_name": payload.last_name,
    }
    try:
        return Persons.create(db, data, commit=False)
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Email already registered") from exc


def get_person(db: Session, person_id: UUID) -> Person:
    return Persons.get(db, str(person_id))


def delete_person(db: Session, person_id: UUID) -> None:
    Persons.delete(db, str(person_id), commit=False)
