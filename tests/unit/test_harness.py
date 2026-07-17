from sqlalchemy import select

from app.core.models import Tenant


def test_tenant_row_visible_in_session(db, tenant_row):
    found = db.scalar(select(Tenant).where(Tenant.id == tenant_row.id))
    assert found is not None
    assert found.slug == "acme"


def test_rollback_isolation(db):
    assert db.scalar(select(Tenant)) is None
