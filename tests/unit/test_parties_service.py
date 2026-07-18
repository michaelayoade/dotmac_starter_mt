"""Unit coverage for `app.features.parties.service` (Task 7).

`Party` (`party_type` person|organization) replaced the bare `Person` model
(Task 6); this feature (Task 7) owns its own API shape —
`create_person_party`/`create_organization_party` each write a `Party` +
subtype row atomically via a single flush (no commit — `get_db` owns the
transaction), and `list_parties` filters by `party_type`.

`update_person_party`/`update_organization_party` (Task 5) close the
`display_name` dual-writer SOT gap — coverage below proves: display_name
recompute on update, party_type immutability (NotFoundError on a
type-mismatched party_id), email uniqueness conflict on update, and the
explicit-null guard on the non-nullable subtype fields
(first_name/last_name/legal_name).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.models import Party, PartyType, Tenant
from app.features.parties import service as parties_service
from app.features.parties.schemas import (
    OrganizationPartyCreate,
    OrganizationPartyUpdate,
    PersonPartyCreate,
    PersonPartyUpdate,
)


def test_create_person_party_creates_party_and_subtype_row(
    db: Session, tenant_row: Tenant
) -> None:
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(
            email="ada@example.com", first_name="Ada", last_name="Lovelace"
        ),
    )

    assert party.party_type == PartyType.person
    assert party.tenant_id == tenant_row.id
    assert party.email == "ada@example.com"
    assert party.display_name == "Ada Lovelace"
    assert party.person_profile is not None
    assert party.person_profile.first_name == "Ada"
    assert party.person_profile.last_name == "Lovelace"
    assert party.organization_profile is None


def test_create_person_party_is_flush_only_rollback_discards_it(
    db: Session, tenant_row: Tenant
) -> None:
    """Carry-over atomicity item: the create paths flush, they never commit —
    `get_db` owns the commit. Proven here by rolling back after the call and
    confirming nothing persisted.
    """
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="rollback@example.com", first_name="R", last_name="B"),
    )
    party_id = party.id

    db.rollback()

    assert db.get(Party, party_id) is None


def test_create_person_party_duplicate_email_raises_conflict(
    db: Session, tenant_row: Tenant
) -> None:
    parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="dup@example.com", first_name="First", last_name="One"),
    )

    with pytest.raises(ConflictError):
        parties_service.create_person_party(
            db,
            tenant_row,
            PersonPartyCreate(
                email="DUP@example.com", first_name="Second", last_name="Two"
            ),
        )


def test_create_person_party_normalizes_email_to_lowercase(
    db: Session, tenant_row: Tenant
) -> None:
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(
            email="MiXeD@ExAmPlE.com", first_name="Mixed", last_name="Case"
        ),
    )

    assert party.email == "mixed@example.com"


def test_create_organization_party_normalizes_email_to_lowercase(
    db: Session, tenant_row: Tenant
) -> None:
    party = parties_service.create_organization_party(
        db,
        tenant_row,
        OrganizationPartyCreate(
            legal_name="MixedCase Org", email="CoNtAcT@ExAmPlE.com"
        ),
    )

    assert party.email == "contact@example.com"


def test_create_organization_party_creates_party_and_subtype_row(
    db: Session, tenant_row: Tenant
) -> None:
    party = parties_service.create_organization_party(
        db, tenant_row, OrganizationPartyCreate(legal_name="Acme Corp Ltd.")
    )

    assert party.party_type == PartyType.organization
    assert party.tenant_id == tenant_row.id
    assert party.email is None
    assert party.display_name == "Acme Corp Ltd."
    assert party.organization_profile is not None
    assert party.organization_profile.legal_name == "Acme Corp Ltd."
    assert party.person_profile is None


def test_create_organization_party_accepts_optional_email(
    db: Session, tenant_row: Tenant
) -> None:
    party = parties_service.create_organization_party(
        db,
        tenant_row,
        OrganizationPartyCreate(
            legal_name="Widgets Inc", email="contact@widgets.example"
        ),
    )

    assert party.email == "contact@widgets.example"


def test_list_parties_filters_by_party_type(db: Session, tenant_row: Tenant) -> None:
    person = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="person@example.com", first_name="P", last_name="Q"),
    )
    org = parties_service.create_organization_party(
        db, tenant_row, OrganizationPartyCreate(legal_name="Org LLC")
    )

    only_people = parties_service.list_parties(
        db, party_type=PartyType.person, limit=50, offset=0
    )
    assert [p.id for p in only_people] == [person.id]

    only_orgs = parties_service.list_parties(
        db, party_type=PartyType.organization, limit=50, offset=0
    )
    assert [p.id for p in only_orgs] == [org.id]

    everyone = parties_service.list_parties(db, party_type=None, limit=50, offset=0)
    assert {p.id for p in everyone} == {person.id, org.id}


def test_list_parties_paginates(db: Session, tenant_row: Tenant) -> None:
    for i in range(3):
        parties_service.create_person_party(
            db,
            tenant_row,
            PersonPartyCreate(
                email=f"page{i}@example.com", first_name="P", last_name=str(i)
            ),
        )

    page = parties_service.list_parties(db, party_type=None, limit=2, offset=0)
    assert len(page) == 2

    rest = parties_service.list_parties(db, party_type=None, limit=2, offset=2)
    assert len(rest) == 1


def test_get_party_returns_party_with_profile_loaded(
    db: Session, tenant_row: Tenant
) -> None:
    created = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="get@example.com", first_name="G", last_name="E"),
    )

    fetched = parties_service.get_party(db, created.id)

    assert fetched.id == created.id
    assert fetched.person_profile is not None
    assert fetched.person_profile.first_name == "G"


def test_get_party_missing_raises_not_found(db: Session, tenant_row: Tenant) -> None:
    with pytest.raises(NotFoundError):
        parties_service.get_party(db, tenant_row.id)


def test_delete_party_removes_person_party(db: Session, tenant_row: Tenant) -> None:
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="del@example.com", first_name="D", last_name="L"),
    )

    parties_service.delete_party(db, party.id)

    assert db.get(Party, party.id) is None


def test_delete_party_removes_organization_party(
    db: Session, tenant_row: Tenant
) -> None:
    """Unlike the old `/people`-only `delete_person`, `/parties` deletion is not
    restricted to person-type parties — organizations are deletable too.
    """
    party = parties_service.create_organization_party(
        db, tenant_row, OrganizationPartyCreate(legal_name="Deletable Org")
    )

    parties_service.delete_party(db, party.id)

    assert db.get(Party, party.id) is None


def test_search_parties_escapes_like_wildcards(db: Session, tenant_row: Tenant) -> None:
    """Searching "50%" matches only literal "50%", not "50X" patterns."""
    # Create two parties: one with "50%" literally, one with "505"
    p1 = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(
            email="50percent@example.com", first_name="50%", last_name="Off"
        ),
    )
    parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="505@example.com", first_name="505", last_name="Name"),
    )

    # Search for "50%" should match only the literal "50%" party
    results = parties_service.search_parties(
        db, q="50%", party_type=None, limit=50, offset=0
    )
    assert len(results) == 1
    assert results[0].id == p1.id


def test_count_parties_escapes_like_wildcards(db: Session, tenant_row: Tenant) -> None:
    """Count also uses escaped wildcards — should match search result count."""
    parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(
            email="50percent@example.com", first_name="50%", last_name="Off"
        ),
    )
    parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="505@example.com", first_name="505", last_name="Name"),
    )

    count = parties_service.count_parties(db, q="50%", party_type=None)
    assert count == 1


# ---------------------------------------------------------------------------
# update_person_party / update_organization_party (Task 5)
# ---------------------------------------------------------------------------


def test_update_person_party_recomputes_display_name(
    db: Session, tenant_row: Tenant
) -> None:
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="ada@example.com", first_name="Ada", last_name="L"),
    )

    updated = parties_service.update_person_party(
        db, party.id, PersonPartyUpdate(last_name="Lovelace")
    )

    assert updated.display_name == "Ada Lovelace"
    assert updated.person_profile.last_name == "Lovelace"
    assert updated.person_profile.first_name == "Ada"  # untouched field preserved


def test_update_person_party_normalizes_email(db: Session, tenant_row: Tenant) -> None:
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="ada@example.com", first_name="Ada", last_name="L"),
    )

    updated = parties_service.update_person_party(
        db, party.id, PersonPartyUpdate(email="MiXeD@ExAmPlE.com")
    )

    assert updated.email == "mixed@example.com"


def test_update_person_party_partial_update_leaves_unset_fields_alone(
    db: Session, tenant_row: Tenant
) -> None:
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="ada@example.com", first_name="Ada", last_name="L"),
    )

    updated = parties_service.update_person_party(db, party.id, PersonPartyUpdate())

    assert updated.person_profile.first_name == "Ada"
    assert updated.person_profile.last_name == "L"
    assert updated.email == "ada@example.com"
    assert updated.display_name == "Ada L"


def test_update_person_party_explicit_null_first_name_raises_bad_request(
    db: Session, tenant_row: Tenant
) -> None:
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="ada@example.com", first_name="Ada", last_name="L"),
    )

    with pytest.raises(BadRequestError):
        parties_service.update_person_party(
            db, party.id, PersonPartyUpdate(first_name=None)
        )


def test_update_person_party_duplicate_email_raises_conflict(
    db: Session, tenant_row: Tenant
) -> None:
    parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(
            email="taken@example.com", first_name="First", last_name="One"
        ),
    )
    party = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="ada@example.com", first_name="Ada", last_name="L"),
    )

    with pytest.raises(ConflictError):
        parties_service.update_person_party(
            db, party.id, PersonPartyUpdate(email="TAKEN@example.com")
        )


def test_update_person_party_on_organization_id_raises_not_found(
    db: Session, tenant_row: Tenant
) -> None:
    org = parties_service.create_organization_party(
        db, tenant_row, OrganizationPartyCreate(legal_name="Acme Corp Ltd.")
    )

    with pytest.raises(NotFoundError):
        parties_service.update_person_party(
            db, org.id, PersonPartyUpdate(first_name="New")
        )


def test_update_organization_party_recomputes_display_name(
    db: Session, tenant_row: Tenant
) -> None:
    org = parties_service.create_organization_party(
        db, tenant_row, OrganizationPartyCreate(legal_name="Acme Corp Ltd.")
    )

    updated = parties_service.update_organization_party(
        db, org.id, OrganizationPartyUpdate(legal_name="Acme Corp International Ltd.")
    )

    assert updated.display_name == "Acme Corp International Ltd."
    assert updated.organization_profile.legal_name == "Acme Corp International Ltd."


def test_update_organization_party_normalizes_email(
    db: Session, tenant_row: Tenant
) -> None:
    org = parties_service.create_organization_party(
        db, tenant_row, OrganizationPartyCreate(legal_name="Acme Corp Ltd.")
    )

    updated = parties_service.update_organization_party(
        db, org.id, OrganizationPartyUpdate(email="CoNtAcT@ExAmPlE.com")
    )

    assert updated.email == "contact@example.com"


def test_update_organization_party_explicit_null_legal_name_raises_bad_request(
    db: Session, tenant_row: Tenant
) -> None:
    org = parties_service.create_organization_party(
        db, tenant_row, OrganizationPartyCreate(legal_name="Acme Corp Ltd.")
    )

    with pytest.raises(BadRequestError):
        parties_service.update_organization_party(
            db, org.id, OrganizationPartyUpdate(legal_name=None)
        )


def test_update_organization_party_duplicate_email_raises_conflict(
    db: Session, tenant_row: Tenant
) -> None:
    parties_service.create_organization_party(
        db,
        tenant_row,
        OrganizationPartyCreate(legal_name="Org One", email="taken@example.com"),
    )
    org = parties_service.create_organization_party(
        db, tenant_row, OrganizationPartyCreate(legal_name="Org Two")
    )

    with pytest.raises(ConflictError):
        parties_service.update_organization_party(
            db, org.id, OrganizationPartyUpdate(email="TAKEN@example.com")
        )


def test_update_organization_party_on_person_id_raises_not_found(
    db: Session, tenant_row: Tenant
) -> None:
    person = parties_service.create_person_party(
        db,
        tenant_row,
        PersonPartyCreate(email="ada@example.com", first_name="Ada", last_name="L"),
    )

    with pytest.raises(NotFoundError):
        parties_service.update_organization_party(
            db, person.id, OrganizationPartyUpdate(legal_name="New Name")
        )
