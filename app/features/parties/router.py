"""Tenant-scoped Parties CRUD.

Demonstrates the canonical pattern: routes never read `tenant_id` from a payload or
URL; it always comes from `request.state.tenant`, set by `TenantResolverMiddleware`,
and enforced at the DB layer by RLS.

A request to `acme.app.com/parties` lists ACME's parties. A request to
`widgets.app.com/parties` with the SAME ID will 404 even if the ID exists in ACME —
because RLS filters it out before the row reaches the application.

Backed by `Party` (`party_type` person|organization) + its subtype table
(`PartyPerson`/`PartyOrganization`) since Task 6 — this feature (Task 7)
replaces the old person-only `/people` surface with `/parties`, which
handles both party types: `POST /parties/people`, `POST
/parties/organizations`, `GET /parties` (paginated, optional `party_type`
filter), `GET /parties/{id}`, `DELETE /parties/{id}`.

Guard pattern matches `app.features.settings.router` / `app.features.rbac.
router` / `app.features.custom_fields.router`: router-level `Depends
(require_tenant)` plus a per-route `Depends(require_role("admin"))` on every
route (final-review Group 2 — mutations were previously reachable by any
authenticated caller, unguarded by role). **Loosen per-route in phase 2b**
once this app grows real UI roles — a plain authenticated user reading their
own tenant's party directory is a reasonable target to loosen first.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.deps import get_db, require_role, require_tenant
from dotmac_kernel.models import Party, PartyType, Tenant
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.features.parties import service as parties_service
from app.features.parties.schemas import (
    OrganizationPartyCreate,
    PartyRead,
    PersonPartyCreate,
)

DEFAULT_PARTIES_LIMIT = 50
MAX_PARTIES_LIMIT = 200

router = APIRouter(
    prefix="/parties",
    tags=["parties"],
    dependencies=[Depends(require_tenant)],
)


@router.post("/people", response_model=PartyRead, status_code=status.HTTP_201_CREATED)
def create_person_party(
    payload: PersonPartyCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> PartyRead:
    party = parties_service.create_person_party(db, tenant, payload)
    return _to_party_read(party)


@router.post(
    "/organizations", response_model=PartyRead, status_code=status.HTTP_201_CREATED
)
def create_organization_party(
    payload: OrganizationPartyCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> PartyRead:
    party = parties_service.create_organization_party(db, tenant, payload)
    return _to_party_read(party)


@router.get("", response_model=list[PartyRead])
def list_parties(
    party_type: PartyType | None = None,
    limit: int = Query(default=DEFAULT_PARTIES_LIMIT, ge=0, le=MAX_PARTIES_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: Party = Depends(require_role("admin")),
) -> list[PartyRead]:
    parties = parties_service.list_parties(
        db, party_type=party_type, limit=limit, offset=offset
    )
    return [_to_party_read(party) for party in parties]


@router.get("/{party_id}", response_model=PartyRead)
def get_party(
    party_id: UUID,
    db: Session = Depends(get_db),
    _: Party = Depends(require_role("admin")),
) -> PartyRead:
    party = parties_service.get_party(db, party_id)
    return _to_party_read(party)


@router.delete(
    "/{party_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
def delete_party(
    party_id: UUID,
    db: Session = Depends(get_db),
    _: Party = Depends(require_role("admin")),
) -> None:
    parties_service.delete_party(db, party_id)


def _to_party_read(party: Party) -> PartyRead:
    """Flatten `Party` + whichever subtype row is populated into `PartyRead`.

    Pure in-memory shaping — `party.person_profile`/`organization_profile`
    are always already loaded by the service layer (eagerly via
    `joinedload` for `list_parties`, or explicitly touched in
    `get_party`/the create paths), so this never triggers a query itself.
    """
    person = party.person_profile
    organization = party.organization_profile
    return PartyRead(
        id=party.id,
        party_type=party.party_type,
        display_name=party.display_name,
        email=party.email,
        first_name=person.first_name if person is not None else None,
        last_name=person.last_name if person is not None else None,
        legal_name=organization.legal_name if organization is not None else None,
    )
