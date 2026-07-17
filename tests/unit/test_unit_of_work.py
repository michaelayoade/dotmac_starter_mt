import pytest
from sqlalchemy import select

from app.core.unit_of_work import UnitOfWork
from app.features.tenants.models import Tenant


def test_uow_commits_on_clean_exit(db):
    with UnitOfWork(db) as uow:
        uow.session.add(Tenant(slug="t1", name="T1"))
    assert db.scalar(select(Tenant).where(Tenant.slug == "t1")) is not None


def test_uow_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with UnitOfWork(db):
            db.add(Tenant(slug="t2", name="T2"))
            raise RuntimeError("boom")
    assert db.scalar(select(Tenant).where(Tenant.slug == "t2")) is None
