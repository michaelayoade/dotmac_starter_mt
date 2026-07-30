import pytest
from dotmac_kernel.crud import CRUDManager
from dotmac_kernel.exceptions import NotFoundError
from dotmac_kernel.models import Party, PartyType


class Parties(CRUDManager[Party]):
    model = Party
    not_found_detail = "Party not found"


def _payload(tenant_row, **over):
    base = {
        "tenant_id": tenant_row.id,
        "party_type": PartyType.person,
        "display_name": "Ada Lovelace",
        "email": "a@example.com",
    }
    base.update(over)
    return base


def test_create_and_get(db, tenant_row):
    row = Parties.create(db, _payload(tenant_row), commit=False)
    assert Parties.get(db, str(row.id)).email == "a@example.com"


def test_update_partial(db, tenant_row):
    row = Parties.create(db, _payload(tenant_row), commit=False)
    updated = Parties.update(
        db, str(row.id), {"display_name": "Grace Hopper"}, commit=False
    )
    assert updated.display_name == "Grace Hopper"
    assert updated.email == "a@example.com"


def test_get_missing_raises_not_found(db):
    with pytest.raises(NotFoundError):
        Parties.get(db, "00000000-0000-0000-0000-000000000000")


def test_delete_hard(db, tenant_row):
    row = Parties.create(db, _payload(tenant_row), commit=False)
    Parties.delete(db, str(row.id), commit=False)
    with pytest.raises(NotFoundError):
        Parties.get(db, str(row.id))


def test_get_malformed_uuid_raises_not_found(db):
    """Verify get() raises NotFoundError for malformed UUIDs."""
    with pytest.raises(NotFoundError):
        Parties.get(db, "not-a-uuid")
