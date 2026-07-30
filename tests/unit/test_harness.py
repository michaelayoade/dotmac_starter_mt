from dotmac_kernel.models import Tenant
from sqlalchemy import select


def test_tenant_row_visible_in_session(db, tenant_row):
    found = db.scalar(select(Tenant).where(Tenant.id == tenant_row.id))
    assert found is not None
    assert found.slug == "acme"


def test_rollback_isolation(db):
    assert db.scalar(select(Tenant)) is None
