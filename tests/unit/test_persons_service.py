"""Unit coverage for `app.features.persons.service` guard logic (Task 6 review).

`Party` (`party_type` person|organization) replaced the bare `Person` model —
the `/people` surface is person-only. `delete_person` must treat an
organization-type party as not-found (consistent with `get_person`'s
existing missing-subtype behavior), not silently delete it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.models import Party, PartyOrganization, PartyType, Tenant
from app.features.persons import service as persons_service


@pytest.fixture()
def org_party(db: Session, tenant_row: Tenant) -> Party:
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.organization,
        display_name="Acme Corp",
    )
    db.add(party)
    db.flush()
    db.add(PartyOrganization(party_id=party.id, legal_name="Acme Corp Ltd."))
    db.flush()
    return party


def test_delete_person_on_organization_party_raises_not_found(
    db: Session, org_party: Party
) -> None:
    with pytest.raises(NotFoundError):
        persons_service.delete_person(db, org_party.id)


def test_delete_person_on_organization_party_does_not_delete_row(
    db: Session, org_party: Party
) -> None:
    with pytest.raises(NotFoundError):
        persons_service.delete_person(db, org_party.id)

    still_there = db.get(Party, org_party.id)
    assert still_there is not None
    assert still_there.party_type == PartyType.organization
