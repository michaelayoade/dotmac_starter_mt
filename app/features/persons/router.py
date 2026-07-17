"""Tenant-scoped Person CRUD.

Demonstrates the canonical pattern: routes never read `tenant_id` from a payload or
URL; it always comes from `request.state.tenant`, set by `TenantResolverMiddleware`,
and enforced at the DB layer by RLS.

A request to `acme.app.com/people` lists ACME's people. A request to
`widgets.app.com/people` with the SAME ID will 404 even if the ID exists in ACME —
because RLS filters it out before the row reaches the application.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_tenant
from app.features.persons import service as persons_service
from app.features.persons.models import Person
from app.features.tenants.models import Tenant

router = APIRouter(
    prefix="/people",
    tags=["people"],
    dependencies=[Depends(require_tenant)],
)


class PersonCreate(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)


class PersonRead(BaseModel):
    id: UUID
    email: EmailStr
    first_name: str
    last_name: str
    model_config = {"from_attributes": True}


@router.post("", response_model=PersonRead, status_code=status.HTTP_201_CREATED)
def create_person(
    payload: PersonCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> Person:
    return persons_service.create_person(db, tenant, payload)


@router.get("", response_model=list[PersonRead])
def list_people(db: Session = Depends(get_db)) -> list[Person]:
    return persons_service.list_persons(db)


@router.get("/{person_id}", response_model=PersonRead)
def get_person(person_id: UUID, db: Session = Depends(get_db)) -> Person:
    return persons_service.get_person(db, person_id)


@router.delete(
    "/{person_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
def delete_person(person_id: UUID, db: Session = Depends(get_db)) -> None:
    persons_service.delete_person(db, person_id)
