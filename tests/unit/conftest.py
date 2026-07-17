"""Fast unit-test fixtures on in-memory SQLite.

RLS does not exist on SQLite — tenancy enforcement is covered by the Postgres
canaries in tests/test_cross_tenant_isolation.py. Unit tests exercise service
logic only and must scope queries explicitly where they care about tenancy.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# Unit tests never touch a real database (SQLite in-memory below), but importing
# a feature's router — e.g. via app.core.features.load_manifests — transitively
# imports app.core.db, which builds a SQLAlchemy engine from DATABASE_URL at
# import time. Set a well-formed placeholder so that import succeeds hermetically.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://unit-test:unit-test@localhost:5432/unit-test"
)

from app.core import audit  # noqa: F401
from app.core.models import Base

# Import model modules so Base.metadata is fully populated.
from app.features.auth import models as auth  # noqa: F401
from app.features.persons import models as person  # noqa: F401
from app.features.rbac import models as rbac  # noqa: F401
from app.features.tenants import models as tenant  # noqa: F401
from app.features.tenants.models import Tenant


@pytest.fixture(scope="session")
def unit_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(unit_engine) -> Generator[Session, None, None]:
    connection = unit_engine.connect()
    outer = connection.begin()
    factory = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = factory()
    # Restart a savepoint whenever service code commits, so the outer
    # rollback still isolates the test.
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture()
def tenant_row(db: Session) -> Tenant:
    row = Tenant(slug="acme", name="Acme")
    db.add(row)
    db.flush()
    return row
