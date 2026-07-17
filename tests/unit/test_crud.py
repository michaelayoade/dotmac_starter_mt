import pytest

from app.core.crud import CRUDManager
from app.core.exceptions import NotFoundError
from app.features.persons.models import Person


class People(CRUDManager[Person]):
    model = Person
    not_found_detail = "Person not found"


def _payload(tenant_row, **over):
    base = {
        "tenant_id": tenant_row.id,
        "email": "a@example.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }
    base.update(over)
    return base


def test_create_and_get(db, tenant_row):
    row = People.create(db, _payload(tenant_row), commit=False)
    assert People.get(db, str(row.id)).email == "a@example.com"


def test_update_partial(db, tenant_row):
    row = People.create(db, _payload(tenant_row), commit=False)
    updated = People.update(db, str(row.id), {"first_name": "Grace"}, commit=False)
    assert updated.first_name == "Grace"
    assert updated.email == "a@example.com"


def test_get_missing_raises_not_found(db):
    with pytest.raises(NotFoundError):
        People.get(db, "00000000-0000-0000-0000-000000000000")


def test_delete_hard(db, tenant_row):
    row = People.create(db, _payload(tenant_row), commit=False)
    People.delete(db, str(row.id), commit=False)
    with pytest.raises(NotFoundError):
        People.get(db, str(row.id))


def test_get_malformed_uuid_raises_not_found(db):
    """Verify get() raises NotFoundError for malformed UUIDs."""
    with pytest.raises(NotFoundError):
        People.get(db, "not-a-uuid")
