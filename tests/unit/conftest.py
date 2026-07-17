"""Fast unit-test fixtures on in-memory SQLite.

RLS does not exist on SQLite — tenancy enforcement is covered by the Postgres
canaries in tests/test_cross_tenant_isolation.py. Unit tests exercise service
logic only and must scope queries explicitly where they care about tenancy.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# DATABASE_URL is pinned to a hermetic placeholder in tests/conftest.py (the
# root conftest), which pytest imports before this module — see the comment
# there about import-time engine creation in app.core.db.
from app.core import audit  # noqa: F401
from app.core.models import Base, Tenant

# Import feature model modules so Base.metadata is fully populated.
from app.features.auth import models as auth  # noqa: F401


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
